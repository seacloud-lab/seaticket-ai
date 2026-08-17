import logging
import requests
import base64

from seaqa_ai.utils import uuid_str_to_36_chars

logger = logging.getLogger(__name__)


def parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    else:
        try:
            return response.json()
        except:
            pass

class SeaDBAPI:
    def __init__(self, server_url, seadb_user, seadb_passwd, timeout=30):
        self.timeout = timeout
        self.server_url = server_url

        self.seadb_user = seadb_user
        self.seadb_passwd = seadb_passwd
        self.headers = None
        self.gen_headers()

    def gen_headers(self):
        auth_str = f"{self.seadb_user}:{self.seadb_passwd}"
        b64 = base64.b64encode(auth_str.encode("utf-8")).decode()
        self.headers = {
            "Authorization": f"Basic {b64}"
        }

    def ping(self):
        url = f'{self.server_url}/ping'
        response = requests.get(url, timeout=self.timeout)
        return parse_response(response)

    def query_rows(self, base_id, sql, params=[], convert_keys=True):
        base_id = uuid_str_to_36_chars(base_id)
        post_data = {
            'sql': sql,
            'convert_keys': convert_keys
        }
        if params:
            post_data['params'] = params
        url = f'{self.server_url}/api/v1/{base_id}/query'
        response = requests.post(url, json=post_data, headers=self.headers, timeout=self.timeout)
        return parse_response(response)
    
    def insert_rows(self, base_id, table_name, rows):
        base_id = uuid_str_to_36_chars(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/rows'
        data = {
            'table_name': str(table_name),
            'rows': rows
        }
        response = requests.post(url, json=data, headers=self.headers, timeout=self.timeout)
        return parse_response(response)

    def update_rows(self, base_id, table_name, rows):
        base_id = uuid_str_to_36_chars(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/rows'
        data = {
            'table_name': table_name,
            'updates': rows
        }
        response = requests.put(url, json=data, headers=self.headers, timeout=self.timeout)
        return parse_response(response)

    def get_base_metadata(self, base_id):
        base_id = uuid_str_to_36_chars(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/metadata'
        response = requests.get(url, headers=self.headers, timeout=self.timeout)
        return parse_response(response)

    def get_records_by_pks(self, base_id, table_name, pks, fields_str='*'):
        """
        return {
            'pk': record
        }
        """
        if fields_str != '*':
            if '_pk' not in fields_str:
                fields_str = '_pk, ' + fields_str

        condition_str = ", ".join([
            str(pk) if not isinstance(pk, str) else f'"{pk}"'
            for pk in pks
        ])

        records = self.query_rows(
            base_id, 
            f'SELECT {fields_str} FROM {table_name} WHERE _pk IN ({condition_str}) AND (`deleted` = False OR `deleted` IS NULL)'
        )['results']

        return {
            record['_pk']: record
            for record in records
        }

    @staticmethod
    def _normalize_fields_str(fields_str, required_fields):
        if fields_str == '*':
            return fields_str

        normalized_fields = []
        seen = set()
        for field in fields_str.split(','):
            normalized_field = field.strip()
            if not normalized_field or normalized_field in seen:
                continue
            seen.add(normalized_field)
            normalized_fields.append(normalized_field)

        for field in required_fields:
            normalized_field = str(field or '').strip()
            if not normalized_field or normalized_field in seen:
                continue
            seen.add(normalized_field)
            normalized_fields.append(normalized_field)

        return ', '.join(normalized_fields)

    @staticmethod
    def _build_topic_entries_sql(table_name, topic_ids, topic_id_field_name, fields_str='*',
                                 has_deleted_field=True, order_by_fields=None):
        condition_str = ', '.join([
            str(topic_id) if not isinstance(topic_id, str) else f'"{topic_id}"'
            for topic_id in topic_ids
        ])

        sql = f'SELECT {fields_str} FROM {table_name} WHERE {topic_id_field_name} IN ({condition_str})'

        if has_deleted_field:
            sql += ' AND (`deleted` = False OR `deleted` IS NULL)'

        normalized_order_by_fields = [field for field in (order_by_fields or []) if field]
        if normalized_order_by_fields:
            sql += ' ORDER BY ' + ', '.join([
                f'`{field}` ASC'
                for field in normalized_order_by_fields
            ])

        return sql

    def _get_topic_entries_grouped(self, base_id, table_name, topic_ids, topic_id_field_name,
                                   fields_str='*', has_deleted_field=True, order_by_fields=None):
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        sql = self._build_topic_entries_sql(
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            order_by_fields=[topic_id_field_name, *(order_by_fields or [])],
        )
        rows = self.query_rows(base_id, sql).get('results', [])

        result = {}
        for row in rows:
            topic_id = row.get(topic_id_field_name)
            if topic_id not in result:
                result[topic_id] = []
            result[topic_id].append(row)

        return result

    @staticmethod
    def _make_record_key(record, key_fields):
        if not key_fields:
            return None
        return tuple(record.get(field) for field in key_fields)

    def _select_first_and_latest_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                               fields_str='*', has_deleted_field=True,
                                               first_order_by_field=None, latest_order_by_field=None,
                                               limit_for_each_topic=20, dedupe_key_fields=None,
                                               sort_key_fields=None):
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        dedupe_key_fields = list(dedupe_key_fields or [])
        sort_key_fields = list(sort_key_fields or dedupe_key_fields or [])
        required_fields = [
            topic_id_field_name,
            first_order_by_field,
            latest_order_by_field,
            *dedupe_key_fields,
            *sort_key_fields,
        ]
        fields_str = self._normalize_fields_str(fields_str, required_fields)

        # Single-query fast path for the common case where both selections use
        # the same ordering semantics.
        if first_order_by_field == latest_order_by_field:
            grouped_rows = self._get_topic_entries_grouped(
                base_id=base_id,
                table_name=table_name,
                topic_ids=topic_ids,
                topic_id_field_name=topic_id_field_name,
                fields_str=fields_str,
                has_deleted_field=has_deleted_field,
                order_by_fields=[first_order_by_field] if first_order_by_field else None,
            )
            return self._build_selected_topic_entries_from_grouped_rows(
                topic_ids=topic_ids,
                grouped_rows=grouped_rows,
                limit_for_each_topic=limit_for_each_topic,
                dedupe_key_fields=dedupe_key_fields,
                sort_key_fields=sort_key_fields,
            )

        first_entries = self.get_first_topic_entries(
            base_id=base_id,
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            order_by_field=first_order_by_field,
        )
        latest_entries_map = self.get_latest_topic_entries(
            base_id=base_id,
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            order_by_field=latest_order_by_field,
            limit_for_each_topic=limit_for_each_topic,
        )
        return self._merge_first_and_latest_topic_entries(
            topic_ids=topic_ids,
            first_entries=first_entries,
            latest_entries_map=latest_entries_map,
            dedupe_key_fields=dedupe_key_fields,
            sort_key_fields=sort_key_fields,
        )

    def _build_selected_topic_entries_from_grouped_rows(self, topic_ids, grouped_rows, limit_for_each_topic,
                                                        dedupe_key_fields=None, sort_key_fields=None):
        first_entries = {}
        latest_entries_map = {}
        for topic_id in topic_ids:
            rows = grouped_rows.get(topic_id, [])
            if rows:
                first_entries[topic_id] = rows[0]
                latest_entries_map[topic_id] = rows[-limit_for_each_topic:] if limit_for_each_topic > 0 else []
            else:
                latest_entries_map[topic_id] = []

        return self._merge_first_and_latest_topic_entries(
            topic_ids=topic_ids,
            first_entries=first_entries,
            latest_entries_map=latest_entries_map,
            dedupe_key_fields=dedupe_key_fields,
            sort_key_fields=sort_key_fields,
        )

    def _merge_first_and_latest_topic_entries(self, topic_ids, first_entries, latest_entries_map,
                                              dedupe_key_fields=None, sort_key_fields=None):
        dedupe_key_fields = list(dedupe_key_fields or [])
        sort_key_fields = list(sort_key_fields or dedupe_key_fields or [])
        result = {}

        for topic_id in topic_ids:
            first_entry = first_entries.get(topic_id)
            latest_entries = latest_entries_map.get(topic_id, [])
            if not first_entry and not latest_entries:
                result[topic_id] = []
                continue

            entries_by_key = {}
            for entry in latest_entries:
                record_key = self._make_record_key(entry, dedupe_key_fields)
                if record_key is None:
                    record_key = id(entry)
                entries_by_key[record_key] = entry

            if first_entry:
                first_entry_key = self._make_record_key(first_entry, dedupe_key_fields)
                if first_entry_key is None:
                    first_entry_key = id(first_entry)
                entries_by_key[first_entry_key] = first_entry

            entries = list(entries_by_key.values())
            if sort_key_fields:
                entries = sorted(
                    entries,
                    key=lambda entry: tuple(entry.get(field) or '' for field in sort_key_fields),
                )
            result[topic_id] = entries

        return result

    @staticmethod
    def _pick_first_entry(entries, first_order_by_field=None):
        entries = list(entries or [])
        if not entries:
            return None
        if not first_order_by_field:
            return entries[0]
        return min(entries, key=lambda entry: entry.get(first_order_by_field) or '')

    def get_first_and_latest_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                           fields_str='*', has_deleted_field=True,
                                           first_order_by_field=None, latest_order_by_field=None,
                                           limit_for_each_topic=20, dedupe_key_fields=None,
                                           sort_key_fields=None):
        return self._select_first_and_latest_topic_entries(
            base_id=base_id,
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            first_order_by_field=first_order_by_field,
            latest_order_by_field=latest_order_by_field,
            limit_for_each_topic=limit_for_each_topic,
            dedupe_key_fields=dedupe_key_fields,
            sort_key_fields=sort_key_fields,
        )

    def get_first_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                fields_str='*', has_deleted_field=True, order_by_field=None):
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        fields_str = self._normalize_fields_str(fields_str, [topic_id_field_name, order_by_field])

        if not order_by_field:
            result = {}
            for topic_id, rows in self._get_topic_entries_grouped(
                base_id=base_id,
                table_name=table_name,
                topic_ids=topic_ids,
                topic_id_field_name=topic_id_field_name,
                fields_str=fields_str,
                has_deleted_field=has_deleted_field,
            ).items():
                if rows:
                    result[topic_id] = rows[0]
            return result

        grouped_rows = self._get_topic_entries_grouped(
            base_id=base_id,
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            order_by_fields=[order_by_field],
        )

        result = {}
        for topic_id, rows in grouped_rows.items():
            if rows:
                result[topic_id] = rows[0]
        return result

    def get_latest_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                 fields_str='*', has_deleted_field=True, order_by_field=None,
                                 limit_for_each_topic=20):
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        fields_str = self._normalize_fields_str(fields_str, [topic_id_field_name, order_by_field])

        if not order_by_field:
            grouped_rows = self._get_topic_entries_grouped(
                base_id=base_id,
                table_name=table_name,
                topic_ids=topic_ids,
                topic_id_field_name=topic_id_field_name,
                fields_str=fields_str,
                has_deleted_field=has_deleted_field,
            )
            return {
                topic_id: grouped_rows.get(topic_id, [])[-limit_for_each_topic:]
                for topic_id in topic_ids
            }

        grouped_rows = self._get_topic_entries_grouped(
            base_id=base_id,
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            order_by_fields=[order_by_field],
        )

        result = {}
        for topic_id in topic_ids:
            result[topic_id] = grouped_rows.get(topic_id, [])[-limit_for_each_topic:]
        return result

    @staticmethod
    def _truncate_content_with_ellipsis(content, max_length):
        content = content or ''
        if max_length <= 0:
            return ''
        if len(content) <= max_length:
            return content
        if max_length <= 3:
            return '.' * max_length
        return content[:max_length - 3] + '...'

    def get_selected_topic_entries(self, base_id, table_name, topic_ids, topic_id_field_name,
                                   fields_str='*', has_deleted_field=True, limit_for_each_topic=20,
                                    first_order_by_field=None, latest_order_by_field=None,
                                     dedupe_key_fields=None, sort_key_fields=None,
                                     initial_content_lengths=None, max_total_content_size=None,
                                     content_field_name='content', more_omitted_notice_builder=None,
                                     prioritize_latest_entry=False):
        """
        Select entries for each topic, subject to an optional content budget.

        Example:
            Given ``limit_for_each_topic=3``, ``max_total_content_size=100``,
            ``initial_content_lengths={'topic-1': 20}``, and entries ordered by
            ``created_at`` as ``[first(30), middle(40), latest(50)]``:

            1. The 20-character initial content consumes part of topic-1's
               100-character budget.
            2. The latest entry has highest priority, then the first entry as
               initial context, followed by other recent entries.
            3. ``latest(50)`` and ``first(30)`` consume the remaining 80
               characters exactly. ``middle(40)`` cannot fit, so it is
               omitted and triggers the optional omitted notice.
            4. Returned records are sorted by ``sort_key_fields`` for display,
               rather than by their selection priority:

            {
                'topic-1': [
                    {
                        'topic_id': 'topic-1',
                        'created_at': '2026-01-01T09:00:00Z',
                        'content': 'Initial context with 30 characters...',
                    },
                    {
                        'topic_id': 'topic-1',
                        'created_at': '2026-01-03T09:00:00Z',
                        'content': 'Most recent update with 50 characters...',
                    },
                    {'content': 'More entries were omitted.'},
                ]
            }

        When ``prioritize_latest_entry`` is true, the newest entry consumes the
        shared budget before stored topic content. The default preserves search
        behavior, which gives stored topic content priority.

        If an entry does not fit in the remaining budget, its content is
        truncated with ``...``. If lower-priority entries are omitted and
        ``more_omitted_notice_builder`` is provided, its result is appended
        after the sorted entries.
        """
        topic_ids = list(topic_ids or [])
        if not topic_ids:
            return {}

        dedupe_key_fields = list(dedupe_key_fields or [])
        sort_key_fields = list(sort_key_fields or dedupe_key_fields or [])
        initial_content_lengths = initial_content_lengths or {}
        required_fields = [
            topic_id_field_name,
            content_field_name,
            first_order_by_field,
            latest_order_by_field,
            *dedupe_key_fields,
            *sort_key_fields,
        ]
        fields_str = self._normalize_fields_str(fields_str, required_fields)

        # Reserve one slot for the earliest entry so the selected context keeps
        # both the initial state and the most recent changes when possible.
        latest_limit = limit_for_each_topic if limit_for_each_topic <= 1 else limit_for_each_topic - 1
        selected_entries_map = self._select_first_and_latest_topic_entries(
            base_id=base_id,
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            first_order_by_field=first_order_by_field,
            latest_order_by_field=latest_order_by_field,
            limit_for_each_topic=latest_limit,
            dedupe_key_fields=dedupe_key_fields,
            sort_key_fields=sort_key_fields,
        )

        result = {}
        for topic_id in topic_ids:
            initial_content_length = int(initial_content_lengths.get(topic_id, 0) or 0)
            entries = list(selected_entries_map.get(topic_id, []))
            if not entries:
                result[topic_id] = []
                continue

            # Apply the entry limit with the newest entry first, while retaining
            # the earliest entry as context when the limit allows it.
            entries = self._prioritize_latest_topic_entries(
                entries,
                first_order_by_field=first_order_by_field,
                sort_key_fields=sort_key_fields,
                limit_for_each_topic=limit_for_each_topic,
            )
            if max_total_content_size is None:
                result[topic_id] = self._sort_topic_entries(entries, sort_key_fields)
                continue

            selected_entries = []
            omitted_notice = None

            latest_entry = entries[0]
            latest_content = latest_entry.get(content_field_name) or ''
            if prioritize_latest_entry:
                # Event-driven callers must see the newest entry even when the
                # stored topic content already exhausts the configured budget.
                if len(latest_content) > max_total_content_size:
                    truncated_entry = dict(latest_entry)
                    truncated_entry[content_field_name] = self._truncate_content_with_ellipsis(
                        latest_content, max_total_content_size,
                    )
                    result[topic_id] = self._sort_topic_entries([truncated_entry], sort_key_fields)
                    continue
                selected_entries.append(latest_entry)
                total_content_size = len(latest_content) + min(
                    initial_content_length,
                    max_total_content_size - len(latest_content),
                )
                entries_to_add = entries[1:]
            else:
                if initial_content_length >= max_total_content_size:
                    result[topic_id] = []
                    continue
                total_content_size = initial_content_length
                entries_to_add = entries

            # Add older context in priority order until the budget is exhausted;
            # truncate the first entry that does not fully fit.
            for entry in entries_to_add:
                content = entry.get(content_field_name) or ''
                remaining_size = max_total_content_size - total_content_size
                if remaining_size <= 0:
                    if more_omitted_notice_builder:
                        omitted_notice = more_omitted_notice_builder()
                    break

                if len(content) > remaining_size:
                    truncated_content = self._truncate_content_with_ellipsis(content, remaining_size)
                    if truncated_content:
                        truncated_entry = dict(entry)
                        truncated_entry[content_field_name] = truncated_content
                        selected_entries.append(truncated_entry)
                    if more_omitted_notice_builder:
                        omitted_notice = more_omitted_notice_builder()
                    break

                selected_entries.append(entry)
                total_content_size += len(content)

            result[topic_id] = self._sort_topic_entries(selected_entries, sort_key_fields)
            # Append a caller-provided marker only when entries were omitted.
            if omitted_notice:
                result[topic_id].append(omitted_notice)

        return result

    @classmethod
    def _prioritize_latest_topic_entries(cls, entries, first_order_by_field=None,
                                         sort_key_fields=None, limit_for_each_topic=20):
        """Return the latest entry, initial context, then other recent entries."""
        if limit_for_each_topic <= 0:
            return []

        sort_key_fields = list(sort_key_fields or [])
        newest_first = sorted(
            entries,
            key=lambda entry: tuple(entry.get(field) or '' for field in sort_key_fields),
            reverse=True,
        )
        if limit_for_each_topic == 1:
            return newest_first[:1]

        first_entry = cls._pick_first_entry(entries, first_order_by_field=first_order_by_field)
        latest_entry = newest_first[0]
        if latest_entry is first_entry:
            return newest_first[:limit_for_each_topic]

        other_recent_entries = [
            entry for entry in newest_first
            if entry is not latest_entry and entry is not first_entry
        ]
        return [latest_entry, first_entry] + other_recent_entries[:limit_for_each_topic - 2]

    @staticmethod
    def _sort_topic_entries(entries, sort_key_fields):
        return sorted(
            entries,
            key=lambda entry: tuple(entry.get(field) or '' for field in sort_key_fields),
        )
