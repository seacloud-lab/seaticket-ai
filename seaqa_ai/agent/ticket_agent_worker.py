"""
TicketAgentWorker: processes open/changed tickets for a single project.

Responsibilities:
1. Query tickets modified since the last agent run, plus tickets due soon.
2. For each ticket, build full context (comments, activities) and run the AI loop.
3. Record analysis and suggestions (notify assignee, add comment) to SeaDB.
"""
import logging
import json
from datetime import datetime, timezone, timedelta

from seaqa_ai import config
from seaqa_ai.agent.base_agent_worker import BaseAgentWorker
from seaqa_ai.agent.tools.ticket_tools import TICKET_TOOL_CLASSES

logger = logging.getLogger(__name__)

SOURCE_TYPE = 'ticket'

DEFAULT_SYSTEM_PROMPT = """You are an autonomous agent that helps manage tickets in a project management system.
You run periodically to analyze tickets and take actions to help the team stay on top of their work.

For each ticket provided in JSON format, follow these steps strictly in order, ONE tool call per step:
1. Call record_ticket_analysis to record your findings about the ticket's current state, any issues, and what action you plan to take.
2. Optionally call notify_assignee if there is a genuine reason (e.g., approaching due date, long inactivity).
3. Optionally call add_comment to suggest a reply or update comment on the ticket.
4. Call final_answer to provide a brief summary of what was done. This MUST be the last tool call.

Rules:
- Always call record_ticket_analysis first before any other actions.
- Call only ONE tool per step. Never call multiple tools in the same step.
- final_answer MUST be called alone, never together with any other tool in the same step.
- Once you call final_answer, you are done. Do NOT call any more tools after final_answer.
- Only notify assignees when there is a genuine reason (approaching the deadline, long inactivity, etc.)
- Do NOT call notify_assignee if previous_notifications shows an already-executed notification for this ticket. This avoids sending duplicate notifications.
- Comment suggestions should be helpful and actionable.
- Be concise in your analysis and only state the most essential points.
- Do NOT repeat the analysis or actions after calling final_answer.

## Input Format

The ticket is provided as a JSON object with the following fields:
- ticket_id: unique identifier
- title: ticket title
- state: current state
- substate: sub-state if applicable
- priority: priority level
- assignees: list of assigned team members
- created_time: when created
- last_modified: last modification time
- due_date: deadline if set
- content: detailed description
- recent_comments: array of recent comments with creator, created_time, content
- recent_activities: array of recent activities with activity_type, created_time, detail
- previous_notifications: array of previously executed notifications with reason and executed_at timestamps. If empty, no previous notifications have been sent.
"""


class TicketAgentWorker(BaseAgentWorker):
    """Processes changed and due-soon tickets for a single project."""

    _DEFAULT_NOTIFY_BEFORE_DUE_HOURS = 48

    TICKETS_TABLE = 'tickets'
    TICKET_COMMENTS_TABLE = 'ticket_comments'
    TICKET_ACTIVITIES_TABLE = 'ticket_activities'

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
        """Fetch comments, activities, and historical notifications for a ticket."""
        ticket_id = item['_pk']

        comments_sql = (
            f"SELECT * FROM `{self.TICKET_COMMENTS_TABLE}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"ORDER BY `_pk` ASC LIMIT 100"
        )
        comments = self.seadb_api.query_rows(
            self.project_uuid, comments_sql
        ).get('results', [])

        activities_sql = (
            f"SELECT * FROM `{self.TICKET_ACTIVITIES_TABLE}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"ORDER BY `created_time` ASC LIMIT 100"
        )
        activities = self.seadb_api.query_rows(
            self.project_uuid, activities_sql
        ).get('results', [])

        # Query historical executed notifications for this ticket
        executed_notifications = self.get_executed_actions(
            source_type=SOURCE_TYPE,
            source_id=str(ticket_id),
            tool_name='notify_assignee'
        )

        return {
            'comments': comments,
            'activities': activities,
            'executed_notifications': executed_notifications,
        }

    def get_executed_actions(self, source_type: str, source_id: str, tool_name: str = None) -> list[dict]:
        """Query executed/confirmed actions for a source record.
        
        Args:
            source_type: Type of the source (e.g., 'ticket')
            source_id: ID of the source record
            tool_name: Optional filter by tool name (e.g., 'notify_assignee')
        
        Returns:
            List of action records ordered by created_at DESC
        """
        conditions = [
            f"`source_type` = '{source_type}'",
            f"`source_id` = '{source_id}'",
            "`status` = 'executed'",
        ]
        if tool_name:
            conditions.append(f"`tool_name` = '{tool_name}'")
        
        where = ' AND '.join(conditions)
        sql = f"SELECT * FROM `{self.AGENT_ACTIONS_TABLE}` WHERE {where} ORDER BY `created_at` DESC LIMIT 10"
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])

    def build_item_message(self, item: dict, item_context: dict) -> str:
        """Build the user message describing the ticket for LLM analysis (JSON format)."""
        
        comments = item_context.get('comments', [])
        formatted_comments = []
        if comments:
            for c in comments[-10:]:
                creator = self.assignee_resolver.resolve_user_for_display(c.get('creator', '?'))
                formatted_comments.append({
                    'creator': creator or '?',
                    'created_time': c.get('created_time', '?'),
                    'content': c.get('content') or ''
                })
        
        activities = item_context.get('activities', [])
        formatted_activities = []
        if activities:
            for a in activities[-10:]:
                detail = self.assignee_resolver.convert_activity_detail(a.get('detail', ''))
                formatted_activities.append({
                    'activity_type': a.get('activity_type', '?'),
                    'created_time': a.get('created_time', '?'),
                    'detail': detail
                })
        
        executed_notifications = item_context.get('executed_notifications', [])
        formatted_notifications = []
        if executed_notifications:
            for n in executed_notifications:
                formatted_notifications.append({
                    'reason': n.get('content', ''),
                    'executed_at': n.get('executed_at') or n.get('created_at', '?'),
                })
        
        message_dict = {
            'ticket_id': item['_pk'],
            'title': item.get('title', 'Untitled'),
            'state': item.get('state', 'N/A'),
            'substate': item.get('substate', 'N/A'),
            'priority': item.get('priority', 'N/A'),
            'assignees': self.assignee_resolver.resolve_assignees_for_display(item.get('assignees')),
            'created_time': item.get('created_time', 'N/A'),
            'last_modified': item.get('modified_time', 'N/A'),
            'due_date': item.get('due_date', 'N/A'),
            'content': item.get('content', '')[:5000] if item.get('content') else '',
            'recent_comments': formatted_comments,
            'recent_activities': formatted_activities,
            'previous_notifications': formatted_notifications,
        }
        
        return json.dumps(message_dict, ensure_ascii=False, indent=2)

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
            f"SELECT * FROM `{self.TICKETS_TABLE}` WHERE {where} "
            f"ORDER BY `modified_time` DESC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])

    def _get_due_soon_tickets(self, hours: int = 48) -> list[dict]:
        """Retrieve open tickets whose due_date falls within the next ``hours`` hours."""
        now = datetime.now(timezone.utc)
        threshold = (now + timedelta(hours=hours)).isoformat()
        sql = (
            f"SELECT * FROM `{self.TICKETS_TABLE}` "
            f"WHERE `due_date` IS NOT NULL AND `due_date` < '{threshold}' "
            f"AND (`state` != 'closed' OR `state` IS NULL) "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"ORDER BY `due_date` ASC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
