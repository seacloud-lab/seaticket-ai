"""
TicketAgent: processes a single ticket for a project.

Responsibilities:
1. Fetch the specified ticket and build full context (comments, activities).
2. Run the AI loop.
3. Record analysis and suggestions (notify assignee, add comment) to SeaDB.
"""
import logging

from seaqa_ai.utils import now_iso, sanitize_project_prompt
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.agent.agents.base_agent import BaseAgent
from seaqa_ai.agent.tools.ticket_tools import RecordTicketAnalysisTool, TICKET_TOOL_CLASSES
from seaqa_ai.agent.utils.constants import (
    MAX_COMMENT_CHARS,
    MAX_COMMENTS,
    MAX_CONTENT_CHARS,
    TICKET_MAX_ACTIVITIES,
)
import seaqa_ai.agent.prompts.ticket as ticket_prompts

logger = logging.getLogger(__name__)


class TicketAgent(BaseAgent):
    """Processes a single ticket for a project."""

    SOURCE_TYPE = 'ticket'

    EVENT_TYPE_PROMPTS = ticket_prompts.PROMPTS

    SOURCE_TOOL_CLASSES = TICKET_TOOL_CLASSES
    ANALYSIS_TERMINATOR_TOOL = RecordTicketAnalysisTool

    def __init__(self, app, project, record_id=None, event=None):
        """
        Args:
            app: the Flask app object.
            project: dict with 'uuid' and 'settings' keys.
            record_id: pk of the ticket to process.
            event: normalized event payload.
        """
        super().__init__(app, project, event)
        self.record_id = record_id

    def get_custom_rules_prompt(self) -> str:
        rules_text = (self.agent_settings.get('ticket_rules') or '').strip()
        if not rules_text:
            return ''
        safe_rules_text = sanitize_project_prompt(rules_text)
        return (
            '<custom_ticket_rules priority="highest">\n'
            'The project owner defined the following Ticket Agent rules.\n'
            'When a rule matches, it overrides default behavior in this prompt.\n'
            'Do not ignore tool-call constraints. In analysis, explicitly cite which rule was applied.\n'
            f'{safe_rules_text}\n'
            '</custom_ticket_rules>'
        )

    # ── Abstract method implementations ────────────────────────────

    def build_record_context(self, item: dict) -> dict:
        return {
            'source_type': self.SOURCE_TYPE,
            'source_id': str(item['_pk']),
            'source_title': item.get('title', 'Untitled'),
        }

    def get_item(self) -> dict | None:
        """Return the single ticket to process, or None if not found."""
        if self.record_id is None:
            return None
        sql = (
            f"SELECT `_pk`, `title`, `state`, `substate`, `type`, `tags`, `priority`, "
            f"`assignees`, `participants`, `created_time`, `modified_time`, `due_date`, `content` "
            f"FROM `{SchemaTables.TICKETS.table_name()}` "
            f"WHERE _pk = {self.record_id} "
            f"AND (`state` != 'closed' OR `state` IS NULL) "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"LIMIT 1"
        )
        results = self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])
        return results[0] if results else None

    def on_run_finished(self, item: dict, success: bool):
        if not success:
            return
        ticket_pk = item.get('_pk')
        if ticket_pk is None:
            return
        self.seadb_api.update_rows(
            self.project_uuid,
            SchemaTables.TICKETS.table_name(),
            [
                {
                    'pk': ticket_pk,
                    'row': {
                        'last_agent_processed_at': now_iso(),
                    },
                }
            ],
        )

    def get_item_context(self, item: dict) -> dict:
        """Fetch comments, activities, and historical notifications for a ticket."""
        ticket_id = item['_pk']

        comments_sql = (
            f"SELECT `creator`, `created_time`, `content` "
            f"FROM `{SchemaTables.TICKET_COMMENTS.table_name()}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"ORDER BY `_pk` DESC LIMIT {MAX_COMMENTS}"
        )
        comments = list(reversed(
            self.seadb_api.query_rows(self.project_uuid, comments_sql).get('results', [])
        ))

        activities_sql = (
            f"SELECT `activity_type`, `created_time`, `detail` "
            f"FROM `{SchemaTables.TICKET_ACTIVITIES.table_name()}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"ORDER BY `created_time` DESC LIMIT {TICKET_MAX_ACTIVITIES}"
        )
        activities = list(reversed(
            self.seadb_api.query_rows(self.project_uuid, activities_sql).get('results', [])
        ))

        # Query historical executed notifications for this ticket
        executed_notifications = self.get_executed_actions(
            source_type=self.SOURCE_TYPE,
            source_id=str(ticket_id),
            tool_name='suggest_notify_assignee'
        )

        return {
            'comments': comments,
            'activities': activities,
            'executed_notifications': executed_notifications,
        }

    def build_record_payload(self, item: dict, item_context: dict) -> dict:
        comments = item_context.get('comments', [])
        formatted_comments = []
        if comments:
            for c in comments[-MAX_COMMENTS:]:
                creator = self.username_resolver.email2nickname(c.get('creator', '?'))
                formatted_comments.append({
                    'creator': creator or '?',
                    'created_time': c.get('created_time', '?'),
                    'content': (c.get('content') or '')[:MAX_COMMENT_CHARS]
                })
        
        activities = item_context.get('activities', [])
        formatted_activities = []
        if activities:
            for a in activities[-TICKET_MAX_ACTIVITIES:]:
                detail = self.username_resolver.convert_usernames_fields_to_nicknames(a.get('detail', ''))
                formatted_activities.append({
                    'activity_type': a.get('activity_type', '?'),
                    'created_time': a.get('created_time', '?'),
                    'detail': detail[:MAX_CONTENT_CHARS]
                })
        
        executed_notifications = item_context.get('executed_notifications', [])
        formatted_notifications = []
        if executed_notifications:
            for n in executed_notifications:
                formatted_notifications.append({
                    'reason': n.get('result', ''),
                    'executed_at': n.get('executed_at') or n.get('created_at', '?'),
                })
        
        message_dict = {
            'ticket_id': item['_pk'],
            'title': item.get('title', 'Untitled'),
            'state': item.get('state', 'N/A'),
            'substate': item.get('substate', 'N/A'),
            'type': item.get('type', 'N/A'),
            'tags': item.get('tags', []),
            'priority': item.get('priority', 'N/A'),
            'assignees': self.username_resolver.emails2nicknames(item.get('assignees')),
            'participants': self.username_resolver.emails2nicknames(item.get('participants')),
            'created_time': item.get('created_time', 'N/A'),
            'last_modified': item.get('modified_time', 'N/A'),
            'due_date': item.get('due_date', 'N/A'),
            'content': item.get('content', '')[:MAX_CONTENT_CHARS] if item.get('content') else '',
            'recent_comments': formatted_comments,
            'recent_activities': formatted_activities,
            'previous_notifications': formatted_notifications,
        }
        return message_dict
