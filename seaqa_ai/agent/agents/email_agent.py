"""
EmailAgent: processes a single email thread for one connection.

1. Build full context: the email thread and recent messages.
2. Run the AI loop.
3. Store tool outcomes as pending/executed actions in SeaDB.
"""
import logging

from seaqa_ai.agent.agents.connection_agent import ConnectionAgent
from seaqa_ai.agent.tools.email_tools import EMAIL_TOOL_CLASSES
from seaqa_ai.agent.utils.constants import (
    MAX_CONTENT_CHARS,
    AgentSourceType,
)
from seaqa_ai.seadb_models import SchemaTables
import seaqa_ai.agent.prompts.email as email_prompts

logger = logging.getLogger(__name__)


class EmailAgent(ConnectionAgent):
    """Processes an email thread for a single email connection within a project."""

    SOURCE_TYPE = AgentSourceType.EMAIL.value

    EVENT_TYPE_PROMPTS = email_prompts.PROMPTS

    SOURCE_TOOL_CLASSES = EMAIL_TOOL_CLASSES

    def __init__(self, app, project, connection_id: int, record_id=None, event=None):
        super().__init__(app, project, connection_id, record_id, event)
        self._threads_table = SchemaTables.THREAD.table_name(self.connection_id)
        self._emails_table = SchemaTables.EMAIL.table_name(self.connection_id)

    # ── Abstract method implementations ────────────────────────────

    def get_item(self) -> dict | None:
        """Return the single email thread to process, or None if not found."""
        if self.record_id is None:
            return None

        conditions = [
            f"_pk = {self.record_id}",
            "(`deleted` = False OR `deleted` IS NULL)",
            "(`linked_ticket` = 0 OR `linked_ticket` IS NULL)",
        ]
        where = ' AND '.join(conditions)
        sql = (
            f"SELECT `_pk`, `title`, `unread`, `modified_time`, `linked_ticket` "
            f"FROM `{self._threads_table}` WHERE {where} LIMIT 1"
        )
        results = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
        return results[0] if results else None

    def get_item_context(self, item: dict) -> dict:
        emails = self._get_thread_emails(item)
        return {'emails': emails}

    def build_record_payload(self, item: dict, item_context: dict) -> dict:
        emails = item_context.get('emails', [])

        first_email = emails[0] if emails else {}
        first_email_content = (first_email.get('content') or '').strip()[:MAX_CONTENT_CHARS]

        recent_emails = []
        for email in emails[-10:]:
            recent_emails.append(self._format_email_entry(email))

        return {
            'title': item.get('title', 'Untitled'),
            'first_email': {
                'email_from': first_email.get('email_from', ''),
                'email_to': first_email.get('email_to', ''),
                'content': first_email_content,
                'modified_time': first_email.get('modified_time', 'N/A'),
            },
            'thread_id': item.get('_pk', 'N/A'),
            'unread': item.get('unread', False),
            'modified_time': item.get('modified_time', 'N/A'),
            'linked_ticket': item.get('linked_ticket', 'N/A'),
            'recent_emails': recent_emails,
        }

    # ── Email-specific helpers ──────────────────────────────────────

    @staticmethod
    def _format_email_entry(email: dict) -> dict:
        return {
            'email_from': email.get('email_from', ''),
            'email_to': email.get('email_to', ''),
            'cc': email.get('cc', ''),
            'modified_time': email.get('modified_time', 'N/A'),
            'is_sender': bool(email.get('is_sender')),
            'content': (email.get('content') or '').strip()[:MAX_CONTENT_CHARS],
        }

    def _get_thread_emails(self, thread: dict) -> list[dict]:
        thread_id = thread.get('_pk')
        if not thread_id:
            return []

        sql = (
            f"SELECT `message_id`, `email_from`, `email_to`, `cc`, "
            f"`modified_time`, `is_sender`, `content` "
            f"FROM `{self._emails_table}` "
            f"WHERE `thread_id` = {thread_id} "
            f"ORDER BY `modified_time` ASC"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
