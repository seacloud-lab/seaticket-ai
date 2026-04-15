"""
EmailAgent: processes a single email thread for one connection.

1. Build full context: the email thread and recent messages.
2. Run the AI loop.
3. Store tool outcomes as pending/executed actions in SeaDB.
"""
import json
import logging

from seaqa_ai.agent.connection_agent import ConnectionAgent
from seaqa_ai.agent.tools.email_tools import EMAIL_TOOL_CLASSES
from seaqa_ai.agent.system_prompt import (
    EMAIL_ANALYSIS_SYSTEM_PROMPT,
    EMAIL_HANDLING_SYSTEM_PROMPT,
)
from seaqa_ai.agent.utils.constants import MAX_COMMENT_CHARS, SourceType
from seaqa_ai.utils.constants import ConnectionType
from seaqa_ai.utils.seadb_api import gen_seadb_table_name

logger = logging.getLogger(__name__)


class EmailAgent(ConnectionAgent):
    """Processes an email thread for a single email connection within a project."""

    SOURCE_TYPE = SourceType.EMAIL.value

    def __init__(self, app, project, connection_id: int, record_id=None, events=None):
        super().__init__(app, project, connection_id, record_id, events)
        self._threads_table, self._emails_table = gen_seadb_table_name(ConnectionType.EMAIL.value, self.connection_id)

    def get_analysis_system_prompt(self) -> str:
        return self._build_system_prompt(EMAIL_ANALYSIS_SYSTEM_PROMPT)

    def get_handling_system_prompt(self) -> str:
        return self._build_system_prompt(EMAIL_HANDLING_SYSTEM_PROMPT)

    def get_tool_classes(self) -> list:
        return EMAIL_TOOL_CLASSES + self.SEARCH_TOOL_CLASSES

    def get_item(self) -> dict | None:
        """Return the single email thread to process, or None if not found."""
        if self.record_id is None:
            return None

        conditions = [
            f"_pk = {self.record_id}",
            "(`deleted` = False OR `deleted` IS NULL)",
            "(`linked_ticket` = 0 OR `linked_ticket` IS NULL)",
            "(`unread` = True OR `unread` IS NULL)",
        ]
        where = ' AND '.join(conditions)
        sql = f"SELECT * FROM `{self._threads_table}` WHERE {where} LIMIT 1"
        results = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
        return results[0] if results else None

    def get_item_context(self, item: dict) -> dict:
        emails = self._get_thread_emails(item)
        return {'emails': emails}

    def build_item_message(self, item: dict, item_context: dict) -> str:
        source_id = f'{self.connection_id}_{item["_pk"]}'

        emails = item_context.get('emails', [])
        formatted_emails = []
        latest_external_email_from = ''
        latest_external_email_to = ''
        latest_external_email_cc = ''
        latest_external_email_content = ''
        latest_external_email_time = item.get('modified_time', 'N/A')

        for email in emails[-10:]:
            content = (email.get('content') or '').strip()[:MAX_COMMENT_CHARS]
            is_sender = bool(email.get('is_sender'))
            email_from = email.get('email_from', '')
            email_to = email.get('email_to', '')
            cc = email.get('cc', '')
            modified_time = email.get('modified_time', 'N/A')

            formatted_emails.append({
                'email_from': email_from,
                'email_to': email_to,
                'cc': cc,
                'modified_time': modified_time,
                'is_sender': is_sender,
                'content': content,
            })

            if not is_sender:
                latest_external_email_from = email_from
                latest_external_email_to = email_to
                latest_external_email_cc = cc
                latest_external_email_content = content
                latest_external_email_time = modified_time

        message_dict = {
            'source_id': source_id,
            'events': self.events,
            'title': item.get('title', 'Untitled'),
            'thread_id': item.get('_pk', 'N/A'),
            'unread': item.get('unread', False),
            'modified_time': item.get('modified_time', 'N/A'),
            'linked_ticket': item.get('linked_ticket', 'N/A'),
            'latest_external_email': {
                'email_from': latest_external_email_from,
                'email_to': latest_external_email_to,
                'cc': latest_external_email_cc,
                'modified_time': latest_external_email_time,
                'content': latest_external_email_content,
            },
            'recent_emails': formatted_emails,
        }

        return json.dumps(message_dict, ensure_ascii=False, indent=2)

    def _get_thread_emails(self, thread: dict) -> list[dict]:
        thread_id = thread.get('_pk')
        if not thread_id:
            return []

        sql = (
            f"SELECT * FROM `{self._emails_table}` "
            f"WHERE `thread_id` = {thread_id} "
            f"ORDER BY `modified_time` ASC"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
