import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True)
class ThreadMessagesQuery:
    """How to fetch the candidate messages of a set of threads."""
    table_name: str
    thread_id_field: str
    order_by: tuple  # ascending time fields, e.g. ('modified_time', 'message_id')
    select_fields: str = '*'
    limit_per_thread: int = 20
    has_deleted_field: bool = True

    def with_select_fields(self, extra_fields):
        """Return a copy selecting extra columns, for a narrower fetch."""
        return dataclasses.replace(
            self,
            select_fields=f'{self.select_fields}, {extra_fields}',
        )


# Predefined queries for the known thread-message tables.
def ticket_comments_query(table_name, limit_per_thread):
    return ThreadMessagesQuery(
        table_name=table_name,
        thread_id_field='ticket_id',
        order_by=('_pk',),
        select_fields='_pk, creator, content',
        limit_per_thread=limit_per_thread,
    )


def portal_issue_comments_query(table_name, limit_per_thread):
    return ThreadMessagesQuery(
        table_name=table_name,
        thread_id_field='issue_id',
        order_by=('_pk',),
        select_fields='_pk, creator, content, created_time, modified_time, via_agent',
        limit_per_thread=limit_per_thread,
    )


def github_issue_comments_query(table_name, limit_per_thread):
    return ThreadMessagesQuery(
        table_name=table_name,
        thread_id_field='issue_id',
        order_by=('comment_id',),
        select_fields='comment_id, author, content, created_time, modified_time',
        limit_per_thread=limit_per_thread,
        has_deleted_field=False,
    )


def discourse_replies_query(table_name, limit_per_thread):
    return ThreadMessagesQuery(
        table_name=table_name,
        thread_id_field='topic_id',
        order_by=('post_number',),
        select_fields='author, post_number, content',
        limit_per_thread=limit_per_thread,
        has_deleted_field=False,
    )


def email_messages_query(table_name, limit_per_thread):
    return ThreadMessagesQuery(
        table_name=table_name,
        thread_id_field='thread_id',
        order_by=('modified_time', 'message_id'),
        select_fields='title, email_from, email_to, cc, content, message_id, modified_time, is_sender',
        limit_per_thread=limit_per_thread,
    )


def linear_issue_comments_query(table_name, limit_per_thread):
    return ThreadMessagesQuery(
        table_name=table_name,
        thread_id_field='issue_id',
        order_by=('created_time',),
        select_fields='comment_id, author, content, created_time',
        limit_per_thread=limit_per_thread,
        has_deleted_field=False,
    )


def jira_issue_comments_query(table_name, limit_per_thread):
    return ThreadMessagesQuery(
        table_name=table_name,
        thread_id_field='issue_id',
        order_by=('comment_id',),
        select_fields='comment_id, author, content, created_time, modified_time',
        limit_per_thread=limit_per_thread,
        has_deleted_field=False,
    )


def discord_thread_messages_query(table_name, limit_per_thread):
    return ThreadMessagesQuery(
        table_name=table_name,
        thread_id_field='thread_id',
        order_by=('created_time', 'message_id'),
        select_fields='message_id, author, content, created_time, modified_time',
        limit_per_thread=limit_per_thread,
        has_deleted_field=False,
    )


class SeaDBQueryService:
    """SeaDB-specific query construction and row retrieval."""

    def __init__(self, api):
        self._api = api

    def get_records_by_pks(self, base_id, table_name, pks, fields_str='*'):
        pks = list(pks or [])
        if not pks:
            return {}
        fields_str = self._normalize_select_fields(fields_str, ['_pk'])
        condition_str = self._build_in_condition(pks)
        rows = self._api.query_rows(
            base_id,
            f'SELECT {fields_str} FROM {table_name} WHERE _pk IN ({condition_str}) '
            'AND (`deleted` = False OR `deleted` IS NULL)',
        ).get('results', [])
        return {row['_pk']: row for row in rows}

    def get_first_and_recent_thread_messages(self, base_id, thread_ids, *, query):
        """Return {thread_id: [messages ascending by query.order_by]},
        keeping the first message plus the most recent limit_per_thread - 1."""
        thread_ids = list(thread_ids or [])
        if not thread_ids:
            return {}

        select_fields = self._normalize_select_fields(
            query.select_fields, [query.thread_id_field, *query.order_by],
        )
        rows = self._api.query_rows(
            base_id, self._build_messages_sql(query, thread_ids, select_fields),
        ).get('results', [])

        messages_by_thread_id = {}
        for row in rows:
            messages_by_thread_id.setdefault(row.get(query.thread_id_field), []).append(row)

        return {
            thread_id: self._first_plus_recent(
                messages_by_thread_id.get(thread_id, []), query.limit_per_thread,
            )
            for thread_id in thread_ids
        }

    def get_first_messages(self, base_id, thread_ids, *, query):
        """Return {thread_id: the single oldest message}, or {} if missing.

        The first message of a thread is the one with the smallest _pk, since
        rows are appended in arrival order. Done in two plain queries because
        the SQL engine does not support a subquery inside IN (...).
        """
        thread_ids = list(thread_ids or [])
        if not thread_ids:
            return {}

        pk_by_thread_id = self._first_pk_by_thread_id(base_id, thread_ids, query)
        if not pk_by_thread_id:
            return {}

        select_fields = self._normalize_select_fields(
            query.select_fields, [query.thread_id_field, '_pk'],
        )
        rows = self._api.query_rows(
            base_id,
            f'SELECT {select_fields} FROM {query.table_name} '
            f'WHERE _pk IN ({self._build_in_condition(pk_by_thread_id.values())})',
        ).get('results', [])
        return {row[query.thread_id_field]: row for row in rows}

    def _first_pk_by_thread_id(self, base_id, thread_ids, query):
        sql = (
            f'SELECT `{query.thread_id_field}`, MIN(_pk) AS _pk FROM {query.table_name} '
            f'WHERE {query.thread_id_field} IN ({self._build_in_condition(thread_ids)})'
        )
        if query.has_deleted_field:
            sql += ' AND (`deleted` = False OR `deleted` IS NULL)'
        sql += f' GROUP BY `{query.thread_id_field}`'
        rows = self._api.query_rows(base_id, sql).get('results', [])
        return {row[query.thread_id_field]: row['_pk'] for row in rows}

    @staticmethod
    def _first_plus_recent(messages, limit_per_thread):
        if limit_per_thread <= 0 or not messages:
            return []
        if limit_per_thread == 1:
            return messages[-1:]
        first_message = messages[:1]
        recent_messages = messages[1:][-(limit_per_thread - 1):]
        return first_message + recent_messages

    @staticmethod
    def _normalize_select_fields(select_fields, required_fields):
        if select_fields == '*':
            return select_fields

        fields = []
        seen = set()
        for field in [*select_fields.split(','), *required_fields]:
            field = str(field or '').strip()
            if field and field not in seen:
                seen.add(field)
                fields.append(field)
        return ', '.join(fields)

    @staticmethod
    def _build_in_condition(values):
        return ', '.join(
            str(value) if not isinstance(value, str) else f'"{value}"'
            for value in values
        )

    @classmethod
    def _build_messages_sql(cls, query, thread_ids, select_fields):
        sql = (
            f'SELECT {select_fields} FROM {query.table_name} WHERE {query.thread_id_field} '
            f'IN ({cls._build_in_condition(thread_ids)})'
        )
        if query.has_deleted_field:
            sql += ' AND (`deleted` = False OR `deleted` IS NULL)'

        order_by_fields = [query.thread_id_field, *[f for f in query.order_by if f]]
        sql += ' ORDER BY ' + ', '.join(f'`{field}` ASC' for field in order_by_fields)
        return sql
