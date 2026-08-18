"""
TicketAgent: processes a single ticket for a project.

Responsibilities:
1. Fetch the specified ticket and build full context (comments, activities).
2. Run the AI loop.
3. Record analysis and suggestions (notify assignee, add comment) to SeaDB.
"""
import logging

from seaqa_ai.utils import now_iso, sanitize_project_prompt, uuid_str_to_32_chars
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.db.models import ProjectConnection
from seaqa_ai.agent.agents.base_agent import BaseAgent
from seaqa_ai.agent.utils import _parse_int
from seaqa_ai.agent.tools.ticket_tools import RecordTicketAnalysisTool, TICKET_TOOL_CLASSES
from seaqa_ai.agent.utils.constants import (
    MAX_COMMENT_CHARS,
    MAX_COMMENTS,
    MAX_CONTENT_CHARS,
    TICKET_MAX_ACTIVITIES,
    DataEventType,
    AgentSourceType
)
from seaqa_ai.utils.constants import ConnectionType
import seaqa_ai.agent.prompts.ticket as ticket_prompts

logger = logging.getLogger(__name__)


class TicketAgent(BaseAgent):
    """Processes a single ticket for a project."""

    SOURCE_TYPE = AgentSourceType.TICKET.value

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

    @property
    def _is_linked_record_activity(self) -> bool:
        return self.event_type == DataEventType.TICKET_LINKED_RECORD_ACTIVITY

    @property
    def _is_task_finished_event(self) -> bool:
        return self.event_type == DataEventType.TICKET_TASK_FINISHED

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

    def build_record_context(self, item: dict, item_context: dict | None = None) -> dict:
        context = {
            'source_type': self.SOURCE_TYPE,
            'source_id': str(item['_pk']),
            'source_title': item.get('title', 'Untitled'),
            'external_reply_targets': [],
        }
        if self._is_linked_record_activity:
            original_source_type = str(self.event.get('original_source_type') or '').strip()
            original_connection_id = self.event.get('original_connection_id')
            original_record_id = self.event.get('original_record_id')

            original_source_id = ''
            if original_connection_id is not None and original_record_id is not None:
                original_source_id = f'{original_connection_id}_{original_record_id}'

            if original_source_type and original_source_id:
                context['external_reply_targets'] = [
                    {
                        'source_type': original_source_type,
                        'source_id': original_source_id,
                        'source_title': str(self.event.get('original_source_title') or ''),
                    }
                ]
        if self._is_task_finished_event:
            context['external_reply_targets'] = (
                (item_context or {}).get('external_reply_targets') or []
            )
        return context

    def get_item(self) -> dict | None:
        """Return the single ticket to process, or None if not found."""
        if self.record_id is None:
            return None
        # Process closed tickets whose linked connection records have new activity,
        # such as new comments on the associated GitHub issue,
        # so that the linked connection record can be replied to.
        # Also handle closed tickets when task finishes (rare but possible).
        state_filter = "AND (`state` != 'closed' OR `state` IS NULL)"
        if self._is_linked_record_activity or self._is_task_finished_event:
            state_filter = ''
        sql = (
            f"SELECT `_pk`, `title`, `state`, `substate`, `type`, `tags`, `priority`, "
            f"`assignees`, `participants`, `linked_connection_records`, "
            f"`created_time`, `modified_time`, `due_date`, `content` "
            f"FROM `{SchemaTables.TICKETS.table_name()}` "
            f"WHERE _pk = {self.record_id} "
            f"{state_filter} "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"LIMIT 1"
        )
        results = self.seadb.query_rows(self.project_uuid, sql).get('results', [])
        return results[0] if results else None

    def on_run_finished(self, item: dict, success: bool):
        if not success:
            return
        ticket_pk = item.get('_pk')
        if ticket_pk is None:
            return
        self.seadb.update_rows(
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
        comments_map = self.seadb.get_selected_topic_entries(
            base_id=self.project_uuid,
            table_name=SchemaTables.TICKET_COMMENTS.table_name(),
            topic_ids=[ticket_id],
            topic_id_field_name='ticket_id',
            fields_str='_pk, creator, created_time, content',
            has_deleted_field=True,
            first_order_by_field='_pk',
            latest_order_by_field='_pk',
            limit_for_each_topic=MAX_COMMENTS,
            dedupe_key_fields=['_pk'],
            sort_key_fields=['_pk'],
        )
        comments = comments_map.get(ticket_id, [])

        activities_sql = (
            f"SELECT `activity_type`, `created_time`, `detail` "
            f"FROM `{SchemaTables.TICKET_ACTIVITIES.table_name()}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"ORDER BY `created_time` DESC LIMIT {TICKET_MAX_ACTIVITIES}"
        )
        activities = list(reversed(
            self.seadb.query_rows(self.project_uuid, activities_sql).get('results', [])
        ))

        # Query historical executed notifications for this ticket
        executed_notifications = self.get_executed_actions(
            source_type=self.SOURCE_TYPE,
            source_id=str(ticket_id),
            tool_name='suggest_notify_assignee'
        )

        context = {
            'comments': comments,
            'activities': activities,
            'executed_notifications': executed_notifications,
        }
        if self._is_linked_record_activity:
            context['linked_record_context'] = self._build_linked_record_context(ticket_id)
        if self._is_task_finished_event:
            context['external_reply_targets'] = self._build_external_reply_targets(item)
        return context

    def build_record_payload(self, item: dict, item_context: dict) -> dict:
        comments = item_context.get('comments', [])
        formatted_comments = []
        if comments:
            for c in comments:
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
        linked_record_context = item_context.get('linked_record_context')
        if isinstance(linked_record_context, dict) and linked_record_context:
            message_dict['linked_record_activity'] = linked_record_context
        if self._is_task_finished_event:
            message_dict['has_external_reply_targets'] = bool(item_context.get('external_reply_targets'))
        return message_dict

    @staticmethod
    def _parse_linked_connection_records(linked_connection_records) -> dict[int, set[int]]:
        connection_map: dict[int, set[int]] = {}
        for linked_key in (linked_connection_records or []):
            if not isinstance(linked_key, str):
                continue
            try:
                connection_id_str, record_id_str = linked_key.split('_', 1)
            except ValueError:
                continue
            if connection_id_str == 'portal':
                continue
            connection_id = _parse_int(connection_id_str)
            record_id = _parse_int(record_id_str)
            if connection_id is None or record_id is None:
                continue
            connection_map.setdefault(connection_id, set()).add(record_id)
        return connection_map

    def _build_external_reply_targets(self, item: dict) -> list[dict]:
        linked_connection_records = item.get('linked_connection_records') or []
        connection_record_ids = self._parse_linked_connection_records(linked_connection_records)
        if not connection_record_ids:
            return []

        connection_ids = list(connection_record_ids.keys())
        project_uuid_32 = uuid_str_to_32_chars(self.project_uuid)
        with self.db_session_class() as db_session:
            connections = ProjectConnection.get_connections_by_ids(
                db_session,
                project_uuid_32,
                connection_ids,
            )
        github_connection_ids = {
            int(connection['id'])
            for connection in connections
            if connection.get('type') == ConnectionType.GITHUB_ISSUE.value
        }
        if not github_connection_ids:
            return []

        external_reply_targets = []
        for connection_id in sorted(github_connection_ids):
            record_ids = sorted(connection_record_ids.get(connection_id) or [])
            issue_rows = self.seadb.get_records_by_pks(
                base_id=self.project_uuid,
                table_name=SchemaTables.GITHUB_ISSUES.table_name(connection_id),
                pks=record_ids,
                fields_str='title',
            )
            for record_id in record_ids:
                issue = (issue_rows or {}).get(record_id) or {}
                external_reply_targets.append({
                    'source_type': ConnectionType.GITHUB_ISSUE.value,
                    'source_id': f'{connection_id}_{record_id}',
                    'source_title': issue.get('title') or 'Untitled',
                })
        return external_reply_targets

    def _build_linked_record_context(self, ticket_id: int) -> dict:
        original_source_type = str(self.event.get('original_source_type') or '').strip()
        original_connection_id = self.event.get('original_connection_id')
        original_record_id = self.event.get('original_record_id')
        original_event_type = str(self.event.get('original_event_type') or '').strip()
        original_source_title = str(self.event.get('original_source_title') or '').strip()
        original_source_id = (
            f'{original_connection_id}_{original_record_id}'
            if original_connection_id is not None and original_record_id is not None
            else ''
        )

        context = {
            'source_type': original_source_type,
            'connection_id': original_connection_id,
            'record_id': original_record_id,
            'source_id': original_source_id,
            'source_title': original_source_title,
            'event_type': original_event_type,
        }

        if original_source_type == AgentSourceType.GITHUB_ISSUE.value:
            issue_context = self._build_linked_github_issue_context(
                ticket_id=ticket_id,
                connection_id=original_connection_id,
                record_id=original_record_id,
            )
            if issue_context:
                context.update(issue_context)
        return context

    def _build_linked_github_issue_context(
        self,
        ticket_id: int,
        connection_id: int,
        record_id: int,
    ) -> dict:
        issues_table = SchemaTables.GITHUB_ISSUES.table_name(connection_id)
        comments_table = SchemaTables.GITHUB_ISSUE_COMMENTS.table_name(connection_id)
        sql = (
            f"SELECT `_pk`, `issue_id`, `title`, `issue_number`, `state`, `author`, "
            f"`labels`, `issue_type`, `url`, `content`, `linked_ticket` "
            f"FROM `{issues_table}` "
            f"WHERE `_pk` = {record_id} "
            f"AND (`deleted` = False OR `deleted` IS NULL) LIMIT 1"
        )
        results = self.seadb.query_rows(self.project_uuid, sql).get('results', [])
        if not results:
            return {}

        issue = results[0]
        raw_linked_ticket = issue.get('linked_ticket')
        if raw_linked_ticket in (None, ''):
            return {}
        try:
            linked_ticket = int(raw_linked_ticket)
        except (TypeError, ValueError):
            return {}
        if linked_ticket != ticket_id:
            return {}

        issue_id = issue.get('issue_id')
        comments = []
        if issue_id:
            comments_map = self.seadb.get_selected_topic_entries(
                base_id=self.project_uuid,
                table_name=comments_table,
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
            comments = comments_map.get(issue_id, [])

        recent_comments = [
            {
                'author': c.get('author', '?'),
                'created_time': c.get('created_time', '?'),
                'content': (c.get('content') or '')[:MAX_COMMENT_CHARS],
            }
            for c in comments
        ]

        return {
            'record': {
                'title': issue.get('title', 'Untitled'),
                'issue_number': issue.get('issue_number', 'N/A'),
                'state': issue.get('state', 'N/A'),
                'author': issue.get('author', 'N/A'),
                'labels': issue.get('labels', []),
                'issue_type': issue.get('issue_type', 'N/A'),
                'url': issue.get('url', 'N/A'),
                'content': (issue.get('content') or '')[:MAX_CONTENT_CHARS],
                'recent_comments': recent_comments,
            },
        }
