from ._seadb_query_service import (
    SeaDBQueryService,
    ThreadMessagesQuery,
    ticket_comments_query,
    portal_issue_comments_query,
    github_issue_comments_query,
    discourse_replies_query,
    email_messages_query,
    linear_issue_comments_query,
    discord_thread_messages_query,
)
from ._thread_content import (
    ContentBudget,
    ContentPriority,
    ThreadContent,
    fit_thread_content_to_budget,
)
from ._seadb_api import SeaDBAPI


class SeaDBService:
    """Application-facing SeaDB entry point for transport and record retrieval."""

    def __init__(self, server_url, seadb_user, seadb_passwd, timeout=30):
        self._api = SeaDBAPI(server_url, seadb_user, seadb_passwd, timeout=timeout)
        self._query_service = SeaDBQueryService(self._api)

    def ping(self):
        return self._api.ping()

    def query_rows(self, base_id, sql, params=None, convert_keys=True):
        return self._api.query_rows(base_id, sql, params=params, convert_keys=convert_keys)

    def insert_rows(self, base_id, table_name, rows):
        return self._api.insert_rows(base_id, table_name, rows)

    def update_rows(self, base_id, table_name, rows):
        return self._api.update_rows(base_id, table_name, rows)

    def get_base_metadata(self, base_id):
        return self._api.get_base_metadata(base_id)

    def get_records_by_pks(self, base_id, table_name, pks, fields_str='*'):
        return self._query_service.get_records_by_pks(base_id, table_name, pks, fields_str)

    def get_thread_content(self, base_id, thread_ids, *, query, budget,
                           bodies_by_thread_id=None, extras_by_thread_id=None):
        """Fetch each thread's candidate messages and fit them with its body into the budget.

        Returns {thread_id: ThreadContent}.
        """
        thread_ids = list(thread_ids or [])
        if not thread_ids:
            return {}
        messages_by_thread_id = self._query_service.get_first_and_recent_thread_messages(
            base_id, thread_ids, query=query,
        )
        bodies_by_thread_id = bodies_by_thread_id or {}
        extras_by_thread_id = extras_by_thread_id or {}
        return {
            thread_id: fit_thread_content_to_budget(
                body=bodies_by_thread_id.get(thread_id),
                messages=messages_by_thread_id.get(thread_id, []),
                budget=budget,
                extras=extras_by_thread_id.get(thread_id),
            )
            for thread_id in thread_ids
        }

    def get_first_thread_messages(self, base_id, thread_ids, *, query):
        """Fetch each thread's single oldest message, in thread order.

        Returns {thread_id: message_row}.
        """
        return self._query_service.get_first_messages(base_id, thread_ids, query=query)


__all__ = [
    'SeaDBService',
    'ThreadMessagesQuery',
    'ticket_comments_query',
    'portal_issue_comments_query',
    'github_issue_comments_query',
    'discourse_replies_query',
    'email_messages_query',
    'linear_issue_comments_query',
    'discord_thread_messages_query',
    'ContentBudget',
    'ContentPriority',
    'ThreadContent',
    'fit_thread_content_to_budget',
]
