"""
GithubIssueAgent: processes a single GitHub issue for one connection.

1. Build full context: issue body + comments.
2. Run the AI loop. The agent calls record_issue_analysis first, then decides next action.
3. Store tool outcomes as pending/executed actions in SeaDB.
"""
import logging
import json

from seaqa_ai.db.models import ProjectConnection
from seaqa_ai.agent.connection_agent import ConnectionAgent
from seaqa_ai.agent.tools.github_issue_tools import GITHUB_ISSUE_TOOL_CLASSES
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.utils.constants import ConnectionType
from seaqa_ai.utils.seadb_api import gen_seadb_table_name
from seaqa_ai.agent.utils.constants import MAX_CONTENT_CHARS, MAX_COMMENT_CHARS
from seaqa_ai.agent.system_prompt import GITHUB_ISSUE_SYSTEM_PROMPT
from seaqa_ai.agent.utils.constants import SourceType

logger = logging.getLogger(__name__)


class GithubIssueAgent(ConnectionAgent):
    """Processes a GitHub issue for a single GitHub connection within a project."""

    SOURCE_TYPE = SourceType.GITHUB_ISSUE.value

    def __init__(self, app, project, connection_id: int, record_id=None, events=None):
        super().__init__(app, project, connection_id, record_id, events)
        self._issues_table, self._comments_table = gen_seadb_table_name(ConnectionType.GITHUB_ISSUE.value, self.connection_id)

    # ── Abstract method implementations ────────────────────────────

    def get_system_prompt(self) -> str:
        return GITHUB_ISSUE_SYSTEM_PROMPT

    def get_tool_classes(self) -> list:
        return GITHUB_ISSUE_TOOL_CLASSES + self.SEARCH_TOOL_CLASSES

    def get_item(self) -> dict | None:
        """Return the single GitHub issue to process, or None if not found."""
        if self.record_id is None:
            return None
        conditions = [
            f"_pk = {self.record_id}",
            "(`state` = 'open' OR `state` IS NULL)",
            "(`deleted` = False OR `deleted` IS NULL)",
            "(`linked_ticket` = 0 OR `linked_ticket` IS NULL)",
        ]
        where = ' AND '.join(conditions)
        sql = f"SELECT * FROM `{self._issues_table}` WHERE {where} LIMIT 1"
        results = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
        return results[0] if results else None


    def get_item_context(self, item: dict) -> dict:
        """Fetch comments for a GitHub issue."""
        comments = self._get_issue_comments(item)
        return {'comments': comments}

    def build_item_message(self, item: dict, item_context: dict) -> str:
        """Build the user message describing the GitHub issue for LLM analysis (JSON format)."""
        
        source_id = f'{self.connection_id}_{item["_pk"]}'
        
        issue_content = (item.get('content') or '').strip()
        
        comments = item_context.get('comments', [])
        formatted_comments = []
        if comments:
            for c in comments[-10:]:
                comment_content = (c.get('content') or '').strip()[:MAX_COMMENT_CHARS]
                formatted_comments.append({
                    'author': c.get('author', '?'),
                    'created_time': c.get('created_time', '?'),
                    'content': comment_content
                })
        
        message_dict = {
            'source_id': source_id,
            'events': self.events,
            'title': item.get('title', 'Untitled'),
            'issue_number': item.get('issue_number', 'N/A'),
            'state': item.get('state', 'N/A'),
            'author': item.get('author', 'N/A'),
            'labels': item.get('labels', 'N/A'),
            'issue_type': item.get('issue_type', 'N/A'),
            'created_time': item.get('created_time', 'N/A'),
            'url': item.get('url', 'N/A'),
            'linked_ticket': item.get('linked_ticket', 'N/A'),
            'content': issue_content[:MAX_CONTENT_CHARS] if issue_content else '',
            'recent_comments': formatted_comments,
        }

        return json.dumps(message_dict, ensure_ascii=False, indent=2)

    # ── GitHub-specific helpers ─────────────────────────────────────

    def _get_issue_comments(self, issue: dict) -> list[dict]:
        """Fetch comments for a single issue ordered by creation time."""
        issue_id = issue.get('issue_id')
        if not issue_id:
            return []

        sql = (
            f"SELECT * FROM `{self._comments_table}` "
            f"WHERE `issue_id` = {issue_id} "
            f"ORDER BY `created_time` ASC"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
