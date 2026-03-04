"""
TicketAgentWorker: processes open/changed tickets for a single project.

Responsibilities:
1. Query tickets modified since the last agent run, plus tickets due soon.
2. For each ticket, build full context (comments, activities) and run the AI loop.
3. Record analysis and suggestions (notify assignee, add comment) to SeaDB.
"""
import logging
from datetime import datetime, timezone, timedelta

from seaqa_ai import config
from seaqa_ai.agent.base_agent_worker import BaseAgentWorker
from seaqa_ai.agent.tools.ticket_tools import TICKET_TOOL_CLASSES

logger = logging.getLogger(__name__)

TICKETS_TABLE = 'tickets'
TICKET_COMMENTS_TABLE = 'ticket_comments'
TICKET_ACTIVITIES_TABLE = 'ticket_activities'

SOURCE_TYPE = 'ticket'

DEFAULT_SYSTEM_PROMPT = """You are an autonomous agent that helps manage tickets in a project management system.
You run periodically to analyze tickets and take actions to help the team stay on top of their work.

For each ticket provided, follow these steps strictly in order, ONE tool call per step:
1. Call analyze_ticket to record your findings about the ticket's current state, any issues, and what action you plan to take.
2. Optionally call notify_assignee if there is a genuine reason (e.g., approaching due date, long inactivity).
3. Optionally call add_comment to suggest a reply or update comment on the ticket.
4. Call final_answer to provide a brief summary of what was done. This MUST be the last tool call.

Rules:
- Always call analyze_ticket first before any other actions.
- Call only ONE tool per step. Never call multiple tools in the same step.
- final_answer MUST be called alone, never together with any other tool in the same step.
- Once you call final_answer, you are done. Do NOT call any more tools after final_answer.
- Only notify assignees when there is a genuine reason (approaching the deadline, long inactivity, etc.)
- Comment suggestions should be helpful and actionable.
- Be concise in your analysis and only state the most essential points.
- Do NOT repeat the analysis or actions after calling final_answer.
"""


class TicketAgentWorker(BaseAgentWorker):
    """Processes changed and due-soon tickets for a single project."""

    _DEFAULT_NOTIFY_BEFORE_DUE_HOURS = 48

    @property
    def notify_before_due_hours(self) -> int:
        return int(
            self.agent_settings.get(
                'notify_before_due_hours', self._DEFAULT_NOTIFY_BEFORE_DUE_HOURS
            )
        )

    # ── Abstract method implementations ────────────────────────────

    def get_system_prompt(self) -> str:
        return DEFAULT_SYSTEM_PROMPT

    def get_tool_classes(self) -> list:
        return TICKET_TOOL_CLASSES

    def build_record_context(self, item: dict) -> dict:
        return {
            'source_type': SOURCE_TYPE,
            'source_id': str(item['_pk']),
            'source_title': item.get('title', 'Untitled'),
        }

    def get_items_to_process(self, since) -> list[dict]:
        """Merge recently changed tickets with due-soon tickets (deduplicated)."""
        changed = self._get_changed_tickets(since)
        due_soon = self._get_due_soon_tickets(self.notify_before_due_hours)

        ticket_map = {}
        for t in changed + due_soon:
            ticket_map[t['_pk']] = t
        return list(ticket_map.values())[:config.AGENT_MAX_TICKETS_PER_RUN]

    def get_item_context(self, item: dict) -> dict:
        """Fetch comments and activities for a ticket."""
        ticket_id = item['_pk']

        comments_sql = (
            f"SELECT * FROM `{TICKET_COMMENTS_TABLE}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"ORDER BY `_pk` ASC LIMIT 100"
        )
        comments = self.seadb_api.query_rows(
            self.project_uuid, comments_sql
        ).get('results', [])

        activities_sql = (
            f"SELECT * FROM `{TICKET_ACTIVITIES_TABLE}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"ORDER BY `created_time` ASC LIMIT 100"
        )
        activities = self.seadb_api.query_rows(
            self.project_uuid, activities_sql
        ).get('results', [])

        return {'comments': comments, 'activities': activities}

    def build_item_message(self, item: dict, item_context: dict) -> str:
        """Build the user message describing the ticket for LLM analysis."""
        parts = [
            f"## Ticket #{item['_pk']}: {item.get('title', 'Untitled')}",
            f"- State: {item.get('state', 'N/A')}",
            f"- Substate: {item.get('substate', 'N/A')}",
            f"- Priority: {item.get('priority', 'N/A')}",
            f"- Assignees: {self.assignee_resolver.resolve_assignees_for_display(item.get('assignees'))}",
            f"- Created: {item.get('created_time', 'N/A')}",
            f"- Last modified: {item.get('modified_time', 'N/A')}",
            f"- Due date: {item.get('due_date', 'N/A')}",
        ]
        if item.get('content'):
            parts.append(f"\n### Content\n{item['content'][:5000]}")

        comments = item_context.get('comments', [])
        if comments:
            parts.append('\n### Recent 10 Comments')
            for c in comments[-10:]:
                creator = self.assignee_resolver.resolve_user_for_display(c.get('creator', '?'))
                parts.append(
                    f"- [{creator or '?'} @ {c.get('created_time', '?')}]: {c.get('content') or ''}"
                )

        activities = item_context.get('activities', [])
        if activities:
            parts.append('\n### Recent 10 Activities')
            for a in activities[-10:]:
                detail = self.assignee_resolver.convert_activity_detail(a.get('detail', ''))
                parts.append(
                    f"- [{a.get('activity_type', '?')} @ {a.get('created_time', '?')}]: {detail}"
                )

        return '\n'.join(parts)

    # ── Ticket-specific queries ─────────────────────────────────────

    def _get_changed_tickets(self, since) -> list[dict]:
        """Retrieve tickets modified since ``since`` that are not closed/deleted."""
        conditions = []
        if since:
            since_iso = since.isoformat()
            conditions.append(f'`modified_time` > "{since_iso}"')

        conditions.append("(`state` != 'closed' OR `state` IS NULL)")
        conditions.append("(`deleted` = False OR `deleted` IS NULL)")

        where = ' AND '.join(conditions)
        sql = (
            f"SELECT * FROM `{TICKETS_TABLE}` WHERE {where} "
            f"ORDER BY `modified_time` DESC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])

    def _get_due_soon_tickets(self, hours: int = 48) -> list[dict]:
        """Retrieve open tickets whose due_date falls within the next ``hours`` hours."""
        now = datetime.now(timezone.utc)
        threshold = (now + timedelta(hours=hours)).isoformat()
        sql = (
            f"SELECT * FROM `{TICKETS_TABLE}` "
            f"WHERE `due_date` IS NOT NULL AND `due_date` < '{threshold}' "
            f"AND (`state` != 'closed' OR `state` IS NULL) "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"ORDER BY `due_date` ASC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
