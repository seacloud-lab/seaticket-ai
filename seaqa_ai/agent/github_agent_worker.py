"""
GithubIssueAgentWorker: processes new GitHub Issues for a single connection.

For each new issue (created since the last run, open, not yet linked to a ticket):
1. Build full context: issue body + comments.
2. Run the AI loop. The agent will call analyze_issue first, then either
   suggest_resolution (known problem) or suggest_create_ticket (new problem).
3. All suggestions are stored as pending actions awaiting user confirmation.
"""
import logging
from datetime import datetime, timezone, timedelta

from seaqa_ai import config
from seaqa_ai.agent.base_agent_worker import BaseAgentWorker
from seaqa_ai.agent.tools.github_issue_tools import GITHUB_ISSUE_TOOL_CLASSES

logger = logging.getLogger(__name__)

SOURCE_TYPE = 'github_issue'

# Maximum body characters sent to the LLM to avoid excessive token usage
MAX_CONTENT_CHARS = 5000
MAX_COMMENT_CHARS = 1000

DEFAULT_SYSTEM_PROMPT = """You are an autonomous agent that triages incoming GitHub Issues for a project.
For each issue provided, follow these steps strictly in order, ONE tool call per step:
1. Call analyze_issue to record your findings and decide whether the issue is a known problem or a new one.
2a. If the issue is a KNOWN problem (can be resolved with existing knowledge), call suggest_resolution with a helpful reply.
2b. If the issue is a NEW problem (not covered by existing knowledge), call suggest_create_ticket with the reason.
3. Call final_answer to summarize what was done. This MUST be the last tool call.

Rules:
- Always call analyze_issue first.
- Call only ONE tool per step. Never call multiple tools in the same step.
- Call either suggest_resolution OR suggest_create_ticket, never both.
- final_answer MUST be the last call. Do NOT call any tool after final_answer.
- Base your known/new judgment on the issue content, title, and any related records provided.
- Be concise and actionable.
"""


class GithubIssueAgentWorker(BaseAgentWorker):
    """Processes new GitHub Issues for a single GitHub connection within a project."""

    def __init__(self, app, project, connection: dict):
        """
        Args:
            app: the Flask app object (carries seadb_api, data_logger, etc.)
            project: dict with 'uuid' and 'settings' keys.
            connection: dict with at least 'id' and 'type' keys from project_connections.
        """
        super().__init__(app, project)
        self.connection = connection
        self.connection_id = connection['id']
        self._issues_table = self._build_issues_table_name()
        self._comments_table = self._build_comments_table_name()

    def _build_issues_table_name(self) -> str:
        return f'github_issue_{self.connection_id}'

    def _build_comments_table_name(self) -> str:
        return f'github_issue_comments_{self.connection_id}'

    # ── Abstract method implementations ────────────────────────────

    def get_system_prompt(self) -> str:
        return DEFAULT_SYSTEM_PROMPT

    def get_tool_classes(self) -> list:
        return GITHUB_ISSUE_TOOL_CLASSES

    def build_record_context(self, item: dict) -> dict:
        """source_id uses the '{connection_id}_{record_pk}' composite format."""
        return {
            'source_type': SOURCE_TYPE,
            'source_id': f'{self.connection_id}_{item["_pk"]}',
            'source_title': item.get('title', 'Untitled'),
        }

    def get_items_to_process(self, since) -> list[dict]:
        """Return new, open, not-yet-linked GitHub issues created since ``since``."""
        logger.info(f"DEBUG: get_items_to_process: since: {since}")
        return self._get_new_issues(since)

    def get_item_context(self, item: dict) -> dict:
        return {'comments': self._get_issue_comments(item)}

    def build_item_message(self, item: dict, item_context: dict) -> str:
        """Build the user message describing the GitHub issue for LLM analysis."""
        source_id = f'{self.connection_id}_{item["_pk"]}'
        parts = [
            f"## GitHub Issue {source_id}: {item.get('title', 'Untitled')}",
            f"- Issue number: #{item.get('issue_number', 'N/A')}",
            f"- State: {item.get('state', 'N/A')}",
            f"- Author: {item.get('author', 'N/A')}",
            f"- Labels: {item.get('labels', 'N/A')}",
            f"- Type: {item.get('issue_type', 'N/A')}",
            f"- Created: {item.get('created_time', 'N/A')}",
            f"- URL: {item.get('url', 'N/A')}",
            f"- Linked ticket: {item.get('linked_ticket', 'N/A')}",
        ]

        content = (item.get('content') or '').strip()
        if content:
            parts.append(f"\n### Issue Body\n{content[:MAX_CONTENT_CHARS]}")

        comments = item_context.get('comments', [])
        if comments:
            parts.append(f'\n### Recent 10 Comments')
            for c in comments[-10:]:
                body = (c.get('content') or '').strip()[:MAX_COMMENT_CHARS]
                parts.append(
                    f"- [{c.get('author', '?')} @ {c.get('created_time', '?')}]: {body}"
                )

        return '\n'.join(parts)

    # ── GitHub-specific queries ─────────────────────────────────────

    def _get_new_issues(self, since) -> list[dict]:
        """Retrieve open issues created after ``since`` that are not yet linked to a ticket."""
        logger.info(f"DEBUG: _get_new_issues: since: {since}")
        conditions = [
            "(`state` = 'open' OR `state` IS NULL)",
            "(`deleted` = False OR `deleted` IS NULL)",
            "(`linked_ticket` = 0 OR `linked_ticket` IS NULL)",
        ]
        if since:
            since_iso = since.isoformat()
            conditions.append(f'`created_time` > "{since_iso}"')

        where = ' AND '.join(conditions)
        sql = (
            f"SELECT * FROM `{self._issues_table}` WHERE {where} "
            f"ORDER BY `created_time` ASC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])

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
