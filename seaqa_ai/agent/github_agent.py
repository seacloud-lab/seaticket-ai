"""
GithubIssueAgent: processes a single GitHub issue for one connection.

1. Build full context: issue body + comments.
2. Run the AI loop. The agent calls record_issue_analysis first, then decides next action.
3. Store tool outcomes as pending/executed actions in SeaDB.
"""
import logging
import json
import datetime
from urllib.parse import quote

from seaqa_ai import config
from seaqa_ai.db.models import Project, ProjectConnection
from seaqa_ai.agent.base_agent import BaseAgent
from seaqa_ai.agent.tools.github_issue_tools import GITHUB_ISSUE_TOOL_CLASSES
from seaqa_ai.managers.chat_manager.tools import DocumentsSearch, IssuesSearch
from seaqa_ai.utils import uuid_str_to_32_chars, gen_record_hash
from seaqa_ai.utils.constants import ConnectionType
from seaqa_ai.utils.seadb_api import gen_seadb_table_name
from seaqa_ai.agent.utils.constants import MAX_CONTENT_CHARS, MAX_COMMENT_CHARS, RELATED_RECORD_SCORE_THRESHOLD
from seaqa_ai.agent.system_prompt import GITHUB_ISSUE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

class GithubIssueAgent(BaseAgent):
    """Processes new GitHub Issues for a single GitHub connection within a project."""

    SOURCE_TYPE = 'github_issue'
    SEARCH_TOOL_NAMES = {'documents_search', 'issues_search'}
    SEARCH_TOOL_CLASSES = [DocumentsSearch, IssuesSearch]

    def __init__(self, app, project, connection_id: int, record_id=None, events=None):
        """
        Args:
            app: the Flask app object (carries seadb_api, data_logger, etc.)
            project: dict with 'uuid' and 'settings' keys.
            connection_id: the ID of the GitHub connection.
            record_id: optional pk of a single issue to process; if None, all eligible issues are processed.
            events: list of event payloads that triggered this agent run.
        """
        super().__init__(app, project)
        self.connection_id = connection_id
        self.record_id = record_id
        self.events = list(events) if events else []
        self._issues_table, self._comments_table = gen_seadb_table_name(ConnectionType.GITHUB_ISSUE.value, self.connection_id)

    # ── Abstract method implementations ────────────────────────────

    def get_system_prompt(self) -> str:
        return GITHUB_ISSUE_SYSTEM_PROMPT

    def get_tool_classes(self) -> list:
        return GITHUB_ISSUE_TOOL_CLASSES + self.SEARCH_TOOL_CLASSES

    def get_tool_registration_kwargs(self, tool_instance, record_context: dict) -> dict:
        tool_name = tool_instance.tool.get('function', {}).get('name', '')
        if tool_name not in self.SEARCH_TOOL_NAMES:
            return {}
        return {
            'context': self._build_search_context(record_context),
            'app': self.app,
        }

    def build_record_context(self, item: dict) -> dict:
        """source_id uses the '{connection_id}_{record_pk}' composite format."""
        return {
            'source_type': self.SOURCE_TYPE,
            'source_id': f'{self.connection_id}_{item["_pk"]}',
            'source_title': item.get('title', 'Untitled'),
        }

    def get_item(self) -> dict | None:
        """Return the single GitHub issue to process, or None if not found."""
        if self.record_id is None:
            return None
        seven_days_ago = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)).isoformat()
        conditions = [
            f"_pk = {self.record_id}",
            "(`state` = 'open' OR `state` IS NULL)",
            "(`deleted` = False OR `deleted` IS NULL)",
            "(`linked_ticket` = 0 OR `linked_ticket` IS NULL)",
            f"`modified_time` >= '{seven_days_ago}'",
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

    # ── GitHub-specific queries ─────────────────────────────────────

    def _build_search_context(self, record_context: dict | None = None) -> dict:
        """Build search context required by documents_search/issues_search tools."""
        cached = getattr(self, '_search_context_cache', None)
        if not isinstance(cached, dict):
            doc_types = ConnectionType.get_document_connection_types()
            issue_types = ConnectionType.get_issue_connection_types()

            document_connections = []
            issue_connections = []
            with self.db_session_class() as db_session:
                project_uuid_32 = uuid_str_to_32_chars(self.project_uuid)
                if doc_types:
                    document_connections = ProjectConnection.get_connections_by_project_and_types(
                        db_session, project_uuid_32, doc_types
                    )
                if issue_types:
                    issue_connections = ProjectConnection.get_connections_by_project_and_types(
                        db_session, project_uuid_32, issue_types
                    )

            cached = self._build_ai_stats_context()
            cached.update({
                'project_uuid': uuid_str_to_32_chars(self.project_uuid),
                'username': 'agent',
                'document_connections': document_connections,
                'issue_connections': issue_connections,
            })
            self._search_context_cache = cached

        context = dict(cached)
        context['skip_search_record_hashes'] = self._build_excluded_search_record_hashes(record_context)
        return context

    def _build_excluded_search_record_hashes(self, record_context: dict | None = None) -> list[str]:
        """Build attachment ids to exclude current processing record from search results."""
        if not isinstance(record_context, dict):
            return []
        source_type = record_context.get('source_type')
        source_id = record_context.get('source_id')
        if not source_type or not isinstance(source_id, str):
            return []
        parts = source_id.split('_', 1)
        if len(parts) != 2:
            return []
        connection_id, record_id = parts
        try:
            connection_id = int(connection_id)
        except Exception:
            return []
        try:
            record_id = int(record_id)
        except Exception:
            pass
        return [gen_record_hash(source_type, connection_id, record_id)]

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

    def _get_search_connection_ids(self) -> list[int]:
        """Return all active ISSUE-category connection ids for this project."""
        with self.db_session_class() as db_session:
            try:
                project_uuid_32 = uuid_str_to_32_chars(self.project_uuid)
                return ProjectConnection.get_connection_ids_by_project_and_types(
                    db_session, project_uuid_32, ConnectionType.get_issue_connection_types()
                )
            except Exception as e:
                logger.warning(f'Failure to query search connections: {e}')
