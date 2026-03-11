"""
GithubIssueAgentWorker: processes new GitHub Issues for a single connection.

For each new issue (created since the last run, open, not yet linked to a ticket):
1. Build full context: issue body + comments + related records (via vector search + reranking).
2. Run the AI loop. The agent will call record_issue_analysis first, then either
   suggest_resolution (known problem) or suggest_create_ticket (new problem).
3. All suggestions are stored as pending actions awaiting user confirmation.
"""
import logging
import json
from urllib.parse import quote

from seaqa_ai import config
from seaqa_ai.db.models import Project, ProjectConnection
from seaqa_ai.agent.base_agent_worker import BaseAgentWorker
from seaqa_ai.agent.tools.github_issue_tools import GITHUB_ISSUE_TOOL_CLASSES
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.agent.constants import MAX_CONTENT_CHARS, MAX_COMMENT_CHARS, RELATED_RECORD_SCORE_THRESHOLD, ISSUE_CONNECTION_TYPES
from seaqa_ai.agent.system_prompt import GITHUB_ISSUE_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

class GithubIssueAgentWorker(BaseAgentWorker):
    """Processes new GitHub Issues for a single GitHub connection within a project."""

    SOURCE_TYPE = 'github_issue'

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
        return GITHUB_ISSUE_SYSTEM_PROMPT

    def get_tool_classes(self) -> list:
        return GITHUB_ISSUE_TOOL_CLASSES

    def build_record_context(self, item: dict) -> dict:
        """source_id uses the '{connection_id}_{record_pk}' composite format."""
        return {
            'source_type': self.SOURCE_TYPE,
            'source_id': f'{self.connection_id}_{item["_pk"]}',
            'source_title': item.get('title', 'Untitled'),
        }

    def get_items_to_process(self, since) -> list[dict]:
        """Return new, open, not-yet-linked GitHub issues created since ``since``."""
        return self._get_new_issues(since)

    def get_item_context(self, item: dict) -> dict:
        """Fetch comments and related record for a GitHub issue."""
        comments = self._get_issue_comments(item)
        related_record = None
        try:
            related_record = self._get_issue_related_record(item)
        except Exception as e:
            logger.warning(f'Failed to get related records for issue {item.get("_pk")}: {e}')
        return {'comments': comments, 'related_record': related_record}

    def build_item_message(self, item: dict, item_context: dict) -> str:
        """Build the user message describing the GitHub issue for LLM analysis (JSON format)."""
        
        source_id = f'{self.connection_id}_{item["_pk"]}'
        
        content = (item.get('content') or '').strip()
        
        comments = item_context.get('comments', [])
        formatted_comments = []
        if comments:
            for c in comments[-10:]:
                content = (c.get('content') or '').strip()[:MAX_COMMENT_CHARS]
                formatted_comments.append({
                    'author': c.get('author', '?'),
                    'created_time': c.get('created_time', '?'),
                    'content': content
                })
        
        related_record = item_context.get('related_record')
        related_record_info = None
        if related_record:
            related_record_info = {
                'type': related_record.get('type', 'N/A'),
                'title': related_record.get('title', 'N/A'),
                'score': related_record.get('score', 'N/A'),
                'url': related_record.get('url', 'N/A'),
                'state': related_record.get('state', 'N/A') if related_record.get('type') == 'github_issue' else None,
                'state_reason': related_record.get('state_reason') if related_record.get('type') == 'github_issue' else None,
                'resolved': related_record.get('resolved', 'N/A') if related_record.get('type') == 'discourse_forum' else None,
                'linked_ticket': related_record.get('linked_ticket') if related_record.get('type') == 'email' else None,
                'linked_ticket_state': related_record.get('linked_ticket_state') if related_record.get('type') == 'email' else None,
                'ai_summary': related_record.get('ai_summary', '')[:500] if related_record.get('ai_summary') else None,
            }
            # Remove None values for cleaner JSON
            related_record_info = {k: v for k, v in related_record_info.items() if v is not None}
        
        message_dict = {
            'source_id': source_id,
            'title': item.get('title', 'Untitled'),
            'issue_number': item.get('issue_number', 'N/A'),
            'state': item.get('state', 'N/A'),
            'author': item.get('author', 'N/A'),
            'labels': item.get('labels', 'N/A'),
            'issue_type': item.get('issue_type', 'N/A'),
            'created_time': item.get('created_time', 'N/A'),
            'url': item.get('url', 'N/A'),
            'linked_ticket': item.get('linked_ticket', 'N/A'),
            'content': content[:MAX_CONTENT_CHARS] if content else '',
            'recent_comments': formatted_comments,
            'most_related_record': related_record_info,
        }

        return json.dumps(message_dict, ensure_ascii=False, indent=2)

    # ── GitHub-specific queries ─────────────────────────────────────

    def _get_new_issues(self, since) -> list[dict]:
        """Retrieve open issues created after ``since`` that are not yet linked to a ticket."""
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

    # ── Related records lookup ──────────────────────────────────────

    def _get_search_connection_ids(self) -> list[int]:
        """Return all active ISSUE-category connection ids for this project."""
        with self.db_session_class() as db_session:
            try:
                project_uuid_32 = uuid_str_to_32_chars(self.project_uuid)
                return ProjectConnection.get_connection_ids_by_project_and_types(
                    db_session, project_uuid_32, ISSUE_CONNECTION_TYPES
                )
            except Exception as e:
                logger.warning(f'Failure to query search connections: {e}')

    def _get_issue_related_record(self, item: dict) -> dict | None:
        """Fetch the top related record for a GitHub issue via vector search + reranking.

        Returns a detail dict (type, score, title, url, state/resolved, …) for the
        highest-ranked record whose score exceeds RELATED_RECORD_SCORE_THRESHOLD,
        or None if no sufficiently similar record is found.
        """
        pk = item.get('_pk')
        title = item.get('title', '')

        sql = f"SELECT ai_summary_vector, ai_summary FROM `{self._issues_table}` WHERE _pk = {pk}"
        rows = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
        if not rows or not rows[0].get('ai_summary_vector'):
            return None

        ai_summary = rows[0].get('ai_summary', '')
        if not ai_summary:
            return None

        query_record = {'title': title, 'ai_summary': ai_summary}
        connection_ids = self._get_search_connection_ids()
        if not connection_ids:
            return None

        search_results = self.app.seaqa_indexer_api.vector_search(
            query=ai_summary,
            project_uuid=self.project_uuid,
            connection_ids=','.join(map(str, connection_ids)),
            count=20,
            username='agent',
        )

        # Exclude the source issue itself from candidates
        filtered = [
            r for r in (search_results or [])
            if not (
                str(r.get('connection_id')) == str(self.connection_id)
                and str(r.get('_id')) == str(pk)
            )
        ]
        if not filtered:
            return None

        reranked_keys = self._rerank_candidates(query_record, filtered)
        if reranked_keys:
            top_key = reranked_keys[0]  # "{pk}:{connection_id}"
            parts = top_key.split(':')
            top_pk, top_conn_id = parts[0], parts[1]
            top_result = next(
                (
                    r for r in filtered
                    if str(r.get('_id')) == top_pk and str(r.get('connection_id')) == top_conn_id
                ),
                filtered[0],
            )
        else:
            top_result = filtered[0]

        score = top_result.get('score', 0) or 0
        if score <= RELATED_RECORD_SCORE_THRESHOLD:
            return None

        return self._fetch_related_record_details(top_result)

    def _rerank_candidates(self, query_record: dict, candidates: list[dict]) -> list[str]:
        """Rerank candidate records using the AI utils relevance ranker.

        Args:
            query_record: dict with 'title' and 'ai_summary' of the query issue.
            candidates: list of vector-search result dicts.

        Returns:
            Reranked key list in the format ["{pk}:{connection_id}", …], or [] on failure.
        """
        if not candidates:
            return []

        try:
            candidate_records = [
                {
                    '_id': c.get('_id'),
                    'connection_id': c.get('connection_id'),
                    'type': c.get('type', 'connection'),
                    'title': c.get('title', ''),
                    'ai_summary': c.get('ai_summary') or c.get('content', ''),
                }
                for c in candidates
            ]

            result = self.app.ai_utils.rank_issues_by_relevance(
                query_record=query_record,
                candidate_records=candidate_records,
                context={
                    'username': 'agent',
                    'org_id': -1,
                    'project_uuid': self.project_uuid,
                },
            )

            ranked_keys = result[0] if isinstance(result, tuple) else result
            return ranked_keys or []
        except Exception as e:
            logger.warning(f'Reranking failed for project {self.project_uuid}, falling back to vector order: {e}')
            return []

    def _fetch_related_record_details(self, result: dict) -> dict | None:
        """Enrich a vector-search result with state/resolved from SeaDB.

        For github_issue records: fetches state, state_reason, url.
        For discourse_forum records: fetches resolved.
        For email records: fetches linked_ticket and linked_ticket_state.
        """
        record_type = result.get('type')
        connection_id = result.get('connection_id')
        pk = result.get('_id')

        details = {
            'type': record_type,
            'score': result.get('score'),
            'title': result.get('title', ''),
            'url': result.get('url', ''),
            'ai_summary': result.get('ai_summary') or result.get('content', ''),
        }

        if record_type == 'github_issue':
            table_name = f'github_issue_{connection_id}'
            sql = f"SELECT `state`, `state_reason`, `url` FROM `{table_name}` WHERE _pk = {pk}"
            rows = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
            if rows:
                details['state'] = rows[0].get('state', '')
                details['state_reason'] = rows[0].get('state_reason', '')
                details['url'] = rows[0].get('url') or details['url']

        elif record_type == 'discourse_forum':
            table_name = f'discourse_forum_{connection_id}'
            sql = f"SELECT `resolved` FROM `{table_name}` WHERE _pk = {pk}"
            rows = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
            if rows:
                details['resolved'] = rows[0].get('resolved', False)

        elif record_type == 'email':
            thread_table_name = f'email_thread_{connection_id}'
            sql = f"SELECT `linked_ticket` FROM `{thread_table_name}` WHERE _pk = {pk}"
            rows = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
            if rows:
                linked_ticket = rows[0].get('linked_ticket')
                details['linked_ticket'] = linked_ticket
                details['url'] = self._build_email_thread_url(connection_id, pk) or details['url']
                if linked_ticket:
                    ticket_sql = (
                        "SELECT `state` FROM `tickets` "
                        f"WHERE _pk = {linked_ticket} AND (`deleted` = False OR `deleted` IS NULL) LIMIT 1"
                    )
                    ticket_rows = self.seadb_api.query_rows(self.project_uuid, ticket_sql).get('results', [])
                    if ticket_rows:
                        ticket_state = (ticket_rows[0].get('state') or '').strip().lower()
                        if ticket_state:
                            details['linked_ticket_state'] = ticket_state

        return details

    def _build_email_thread_url(self, connection_id, thread_pk) -> str:
        """Build absolute SeaQA web URL for a connection record page."""
        project_meta = self._get_project_meta_for_web_url()
        workspace_id = project_meta.get('workspace_id')
        project_name = project_meta.get('name')
        if not workspace_id or not project_name:
            return ''

        encoded_project_name = quote(str(project_name), safe='')
        web_service_url = (config.SEAQA_WEB_SERVICE_URL or '').rstrip('/')
        if not web_service_url:
            return ''
        return (
            f"{web_service_url}/workspace/{workspace_id}/project/{encoded_project_name}/"
            f"connections/{connection_id}/records/{thread_pk}/"
        )

    def _get_project_meta_for_web_url(self) -> dict:
        """Return project metadata needed to build SeaQA web links."""
        cached = getattr(self, '_project_meta_cache', None)
        if isinstance(cached, dict):
            return cached

        with self.db_session_class() as db_session:
            try:
                project_uuid_32 = uuid_str_to_32_chars(self.project_uuid)
                meta = Project.get_project_metadata(db_session, project_uuid_32)
            except Exception as e:
                logger.warning(
                    f'Failed to fetch project metadata for web url, project {self.project_uuid}: {e}'
                )
                meta = {'name': None, 'workspace_id': None}

        self._project_meta_cache = meta
        return meta
