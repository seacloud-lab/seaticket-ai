"""
GithubIssueAgent: processes a single GitHub issue for one connection.

1. Build full context: issue body + comments.
2. Run the AI loop. The agent calls record_issue_analysis first, then decides next action.
3. Store tool outcomes as pending/executed actions in SeaDB.
"""
from seaqa_ai.agent.agents.connection_agent import ConnectionAgent
from seaqa_ai.agent.tools.github_issue_tools import GITHUB_ISSUE_TOOL_CLASSES
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.agent.utils.constants import (
    MAX_CONTENT_CHARS,
    MAX_COMMENT_CHARS,
    MAX_COMMENTS,
    AgentSourceType,
)
import seaqa_ai.agent.prompts.github_issue as github_issue_prompts

class GithubIssueAgent(ConnectionAgent):
    """Processes a GitHub issue for a single GitHub connection within a project."""

    SOURCE_TYPE = AgentSourceType.GITHUB_ISSUE.value

    EVENT_TYPE_PROMPTS = github_issue_prompts.PROMPTS

    SOURCE_TOOL_CLASSES = GITHUB_ISSUE_TOOL_CLASSES

    def __init__(self, app, project, connection_id: int, record_id=None, event=None):
        super().__init__(app, project, connection_id, record_id, event)
        self._issues_table = SchemaTables.GITHUB_ISSUES.table_name(self.connection_id)
        self._comments_table = SchemaTables.GITHUB_ISSUE_COMMENTS.table_name(self.connection_id)

    # ── Abstract method implementations ────────────────────────────

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
        sql = (
            f"SELECT `_pk`, `issue_id`, `title`, `content`, `issue_number`, "
            f"`state`, `author`, `labels`, `issue_type`, `created_time`, "
            f"`url`, `linked_ticket` "
            f"FROM `{self._issues_table}` WHERE {where} LIMIT 1"
        )
        results = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
        return results[0] if results else None

    def get_item_context(self, item: dict) -> dict:
        """Fetch comments for a GitHub issue."""
        comments = self._get_issue_comments(item)
        return {'comments': comments}

    def build_record_payload(self, item: dict, item_context: dict) -> dict:
        issue_content = (item.get('content') or '').strip()
        comments = item_context.get('comments', [])
        available_labels = self._get_available_labels()

        recent_comments = [self._format_comment_entry(c) for c in comments]

        return {
            'title': item.get('title', 'Untitled'),
            'issue_number': item.get('issue_number', 'N/A'),
            'state': item.get('state', 'N/A'),
            'author': item.get('author', 'N/A'),
            'labels': item.get('labels', 'N/A'),
            'available_labels': available_labels,
            'issue_type': item.get('issue_type', 'N/A'),
            'created_time': item.get('created_time', 'N/A'),
            'url': item.get('url', 'N/A'),
            'linked_ticket': item.get('linked_ticket', 'N/A'),
            'content': issue_content[:MAX_CONTENT_CHARS] if issue_content else '',
            'recent_comments': recent_comments,
        }

    # ── GitHub-specific helpers ─────────────────────────────────────

    @staticmethod
    def _format_comment_entry(c: dict) -> dict:
        return {
            'author': c.get('author', '?'),
            'created_time': c.get('created_time', '?'),
            'content': (c.get('content') or '').strip()[:MAX_COMMENT_CHARS],
        }

    def _get_issue_comments(self, issue: dict) -> list[dict]:
        """Fetch first comment plus recent comments for a single issue."""
        issue_id = issue.get('issue_id')
        if not issue_id:
            return []

        comments_map = self.seadb_api.get_first_and_latest_topic_entries(
            base_id=self.project_uuid,
            table_name=self._comments_table,
            topic_ids=[issue_id],
            topic_id_field_name='issue_id',
            fields_str='comment_id, author, created_time, content',
            has_deleted_field=False,
            first_order_by_field='comment_id',
            latest_order_by_field='comment_id',
            limit_for_each_topic=MAX_COMMENTS,
            dedupe_key_fields=['comment_id'],
            sort_key_fields=['comment_id'],
        )
        return comments_map.get(issue_id, [])

    def _get_available_labels(self) -> list[str]:
        cached = getattr(self, '_available_labels_cache', None)
        if isinstance(cached, list):
            return cached

        available_labels = []
        try:
            metadata = self.seadb_api.get_base_metadata(self.project_uuid)
            tables = (metadata or {}).get('tables') or []
            table_meta = next(
                (table for table in tables if table.get('name') == self._issues_table),
                None,
            )
            if table_meta:
                label_column = next(
                    (
                        column for column in (table_meta.get('columns') or [])
                        if column.get('name') == 'labels'
                    ),
                    None,
                )
                options = ((label_column or {}).get('data') or {}).get('options') or []
                seen = set()
                for option in options:
                    name = str(option.get('name') or '').strip()
                    key = name.lower()
                    if not name or key in seen:
                        continue
                    seen.add(key)
                    available_labels.append(name)
        except Exception:
            available_labels = []

        self._available_labels_cache = available_labels
        return available_labels
