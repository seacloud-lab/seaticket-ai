"""
GithubIssueAgentWorker: processes new GitHub Issues for a single connection.

For each new issue (created since the last run, open, not yet linked to a ticket):
1. Build full context: issue body + comments + related records (via vector search + reranking).
2. Run the AI loop. The agent will call analyze_issue first, then either
   suggest_resolution (known problem) or suggest_create_ticket (new problem).
3. All suggestions are stored as pending actions awaiting user confirmation.
"""
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from seaqa_ai import config
from seaqa_ai.agent.base_agent_worker import BaseAgentWorker
from seaqa_ai.agent.tools.github_issue_tools import GITHUB_ISSUE_TOOL_CLASSES
from seaqa_ai.utils import uuid_str_to_32_chars

logger = logging.getLogger(__name__)

SOURCE_TYPE = 'github_issue'

# Maximum body characters sent to the LLM to avoid excessive token usage
MAX_CONTENT_CHARS = 5000
MAX_COMMENT_CHARS = 1000

# Connection types considered as ISSUE category for related records search
ISSUE_CONNECTION_TYPES = ('github_issue', 'discourse_forum', 'email')

# Minimum similarity score to consider a related record as a known issue
RELATED_RECORD_SCORE_THRESHOLD = 0.5

DEFAULT_SYSTEM_PROMPT = """You are an autonomous agent that triages incoming GitHub Issues for a project.

For each issue provided, a "Most Related Record" section may be included. Use it to determine if the issue is a known problem.

## Known Issue Detection Rules

1. If NO related record is provided → This is a NEW problem → Call suggest_create_ticket.

2. If a related record IS provided:

   a) If type is "github_issue":
      - If state is "closed": This is a KNOWN and RESOLVED problem.
        → Call suggest_resolution. Your resolution MUST include the related issue URL.
        → Suggest the user close this issue as a duplicate, referencing the related issue.
      - If state is "open": This is a KNOWN but UNRESOLVED problem.
        → Call suggest_resolution. Your resolution MUST include the related issue URL.
        → Mention that this is a known issue already being tracked.

   b) If type is "discourse_forum":
      - If resolved is "true": This is a KNOWN and RESOLVED problem.
        → Call suggest_resolution. Your resolution MUST include the forum topic URL.
        → Reference the forum discussion for the solution.
      - If resolved is "false": This is a KNOWN but UNRESOLVED problem.
        → Call suggest_resolution. Your resolution MUST include the forum topic URL.
        → Mention that there is an ongoing discussion about this issue.

## Processing Steps (ONE tool call per step, strictly in order)

1. Call analyze_issue to record your findings based on the related record info.
2a. If KNOWN problem → Call suggest_resolution with a helpful reply that ALWAYS includes the related record URL.
2b. If NEW problem → Call suggest_create_ticket with the reason.
3. Call final_answer to summarize what was done. This MUST be the last tool call.

Rules:
- Always call analyze_issue first.
- Call only ONE tool per step. Never call multiple tools in the same step.
- Call either suggest_resolution OR suggest_create_ticket, never both.
- final_answer MUST be the last call. Do NOT call any tool after final_answer.
- When writing a resolution, ALWAYS include the related record URL in the reply text.
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
        return self._get_new_issues(since)

    def get_item_context(self, item: dict) -> dict:
        """Fetch comments and related record for a GitHub issue."""
        comments = self._get_issue_comments(item)
        related_record = None
        try:
            related_record = self._get_issue_related_records(item)
        except Exception as e:
            logger.warning(f'Failed to get related records for issue {item.get("_pk")}: {e}')
        return {'comments': comments, 'related_record': related_record}

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

        related_record = item_context.get('related_record')
        if related_record:
            parts.append('\n### Most Related Record')
            parts.append(f"- Type: {related_record.get('type', 'N/A')}")
            parts.append(f"- Title: {related_record.get('title', 'N/A')}")
            parts.append(f"- Score: {related_record.get('score', 'N/A')}")
            parts.append(f"- URL: {related_record.get('url', 'N/A')}")
            if related_record.get('type') == 'github_issue':
                parts.append(f"- State: {related_record.get('state', 'N/A')}")
                if related_record.get('state_reason'):
                    parts.append(f"- State Reason: {related_record.get('state_reason')}")
            elif related_record.get('type') == 'discourse_forum':
                parts.append(f"- Resolved: {related_record.get('resolved', 'N/A')}")
            if related_record.get('ai_summary'):
                parts.append(f"- Summary: {related_record.get('ai_summary', '')[:500]}")

        return '\n'.join(parts)

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
        type_placeholders = ', '.join(f"'{t}'" for t in ISSUE_CONNECTION_TYPES)
        session = self.db_session_class()
        try:
            result = session.execute(
                text(
                    f"SELECT id FROM project_connection "
                    f"WHERE project_uuid = :uuid AND type IN ({type_placeholders}) "
                    f"AND deleted = 0 AND is_active = 1"
                ),
                {'uuid': uuid_str_to_32_chars(self.project_uuid)},
            )
            return [row[0] for row in result.fetchall()]
        finally:
            session.close()

    def _get_issue_related_records(self, item: dict) -> dict | None:
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

        return details
