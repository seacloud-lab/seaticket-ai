"""
ConnectionAgent: intermediate base class for connection-backed agents.
"""
from seaqa_ai.db.models import ProjectConnection
from seaqa_ai.agent.agents.base_agent import BaseAgent
from seaqa_ai.utils.tools import DocumentsSearch, IssuesSearch, SEARCH_TOOL_NAMES
from seaqa_ai.utils import uuid_str_to_32_chars, gen_record_hash
from seaqa_ai.utils.constants import ConnectionType


class ConnectionAgent(BaseAgent):
    """
    Intermediate base for agents that process a single record from a
    project connection (e.g. GitHub Issue, Discourse Forum, Email Thread).

    Adds search-tool injection on top of BaseAgent's tool assembly:
    search tools (DocumentsSearch, IssuesSearch) are appended via
    get_tool_classes() when SEARCH_ENABLED is True for this agent.
    """

    SOURCE_TYPE: str = ''
    SEARCH_ENABLED = True

    _SEARCH_TOOL_CLASSES: list = [DocumentsSearch, IssuesSearch]

    def __init__(self, app, project, connection_id: int, record_id=None, event=None):
        super().__init__(app, project, event)
        self.connection_id = connection_id
        self.record_id = record_id

    # ── BaseAgent hook implementations ─────────────────────────────

    def build_record_context(self, item: dict, item_context: dict | None = None) -> dict:
        return {
            'source_type': self.SOURCE_TYPE,
            'source_id': f'{self.connection_id}_{item["_pk"]}',
            'source_title': item.get('title', 'Untitled'),
        }

    def get_tool_classes(self) -> list:
        """Append search tool classes when search is enabled for this agent."""
        tools = super().get_tool_classes()
        if not self.SEARCH_ENABLED:
            return tools
        for tool_cls in self._SEARCH_TOOL_CLASSES:
            if tool_cls not in tools:
                tools.append(tool_cls)
        return tools

    def get_tool_registration_kwargs(self, tool_instance, record_context: dict) -> dict:
        tool_name = tool_instance.tool.get('name', '')
        if tool_name not in SEARCH_TOOL_NAMES:
            return {}
        return {
            'context': self._build_search_context(record_context),
            'app': self.app,
        }

    # ── Search context helpers ──────────────────────────────────────

    def _build_search_context(self, record_context: dict | None = None) -> dict:
        """Build search context required by documents_search/issues_search tools.

        Results are cached per agent instance; only skip_search_record_hashes
        varies per call (to exclude the current record from search results).
        """
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

    def _build_excluded_search_record_hashes(self, record_context: dict | None = None) -> list:
        """Return record hashes that should be excluded from search results. Excludes the current record."""
        if not isinstance(record_context, dict):
            return []
        source_type = record_context.get('source_type')
        source_id = record_context.get('source_id')
        if not source_type or not isinstance(source_id, str):
            return []
        parts = source_id.split('_', 1)
        if len(parts) != 2:
            return []
        connection_id_str, record_id_str = parts
        try:
            connection_id = int(connection_id_str)
        except Exception:
            return []
        try:
            record_id = int(record_id_str)
        except Exception:
            record_id = record_id_str
        return [gen_record_hash(source_type, connection_id, record_id)]
