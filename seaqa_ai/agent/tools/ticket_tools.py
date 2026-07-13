"""
Agent tools specific to ticket processing.

Tools:
- RecordTicketAnalysisTool: records the agent's analysis of a ticket
  (used as the analysis terminator for some ticket event classes; injected
  per event class by BaseAgent and NOT listed in TICKET_TOOL_CLASSES).
- SuggestNotifyAssigneeTool: suggests notifying ticket assignees (pending user confirmation)
"""
import logging
from seaqa_ai.agent.tools.common_tools import BaseAgentTool
from seaqa_ai.agent.utils.constants import AgentSourceType

logger = logging.getLogger(__name__)


class InvalidValueException(Exception):
    """Raised when a required tool argument is missing or invalid."""


class RecordTicketAnalysisTool(BaseAgentTool):
    """Records the agent's analysis of a ticket before taking any action."""

    tool = {
        'type': 'function',
        'name': 'record_ticket_analysis',
        'description': (
            'Record your analysis of the current ticket situation. '
            'Always call this tool first before taking any other actions. '
            'Use it to document your findings, reasoning, and any concerns.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'analysis': {
                    'type': 'string',
                    'description': 'Your detailed analysis of the ticket: current state, issues found, and what action you plan to take. Use straight quotes around any UI labels or feature names as needed; output the value as plain prose — do not additionally JSON-escape characters in your answer.'
                },
                'recommended_actions': {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                        'enum': [
                            'suggest_notify_assignee',
                            'suggest_close_ticket',
                            'suggest_reply_linked_record',
                            'no_op',
                        ],
                    },
                    'description': 'List of recommended actions. Use ["no_op"] when no handling action is needed.',
                },
            },
            'required': ['analysis', 'recommended_actions']
        },
        'strict': True,
    }

    action_type = 'analysis'
    action_status = 'completed'

    def execute(self, analysis, recommended_actions):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        actions = recommended_actions if isinstance(recommended_actions, list) else []
        actions_display = ', '.join([str(action) for action in actions]) if actions else 'no_op'
        return (
            f'Analysis recorded for ticket #{source_id} - {source_title}. '
            f'Recommended actions: {actions_display}.'
        )


class SuggestNotifyAssigneeTool(BaseAgentTool):
    """Suggests notifying the assignees of a ticket; requires user confirmation."""

    tool = {
        'type': 'function',
        'name': 'suggest_notify_assignee',
        'description': (
            'Send a notification to the assignees of a ticket. '
            'Use this when a ticket is approaching its due date, '
            'has been inactive for too long, or needs attention. '
            'This action is usually pending for user confirmation, '
            'but some projects may configure it to auto-execute.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'message': {
                    'type': 'string',
                    'description': 'The notification message to send to assignees'
                },
                'reason': {
                    'type': 'string',
                    'description': 'The reason for the notification (e.g., "approaching_due_date")'
                }
            },
            'required': ['message']
        },
        'strict': True,
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Notify assignees of the ticket.'

    def execute(self, message, reason=None):
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        message = (message or '').strip()
        reason = (reason or 'unspecified').strip()

        if not message:
            return 'Cannot notify assignee: empty message content.'

        preview = message[:300]
        if len(message) > 300:
            preview += '...'

        return (
            f'Notification suggestion prepared for ticket #{source_id} - {source_title} '
            f'(reason: {reason}): {preview}'
        )


class SuggestCloseTicketTool(BaseAgentTool):
    """Suggests closing a ticket; requires user confirmation."""

    tool = {
        'type': 'function',
        'name': 'suggest_close_ticket',
        'description': (
            'Suggest closing the current ticket with a concise reason. '
            'This suggestion requires user confirmation before execution.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'reason': {
                    'type': 'string',
                    'description': 'The reason for closing this ticket'
                },
                'rule': {
                    'type': 'string',
                    'description': 'Optional matched custom rule text'
                }
            },
            'required': ['reason']
        },
        'strict': True,
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Close this ticket.'

    def execute(self, reason, rule=None):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        reason = (reason or '').strip()
        rule = (rule or '').strip()
        if not reason:
            raise InvalidValueException('Cannot suggest closing ticket: empty reason.')

        return (
            f'Close-ticket suggestion prepared for ticket #{source_id} - {source_title}. '
            f'Reason: {reason}. Rule: {rule}'
        )


class SuggestLinkedRecordReplyTool(BaseAgentTool):
    """Suggests a public reply on a linked external source record."""

    tool = {
        'type': 'function',
        'name': 'suggest_reply_linked_record',
        'description': (
            'Suggest replying to an external source record linked to this ticket. '
            'Use this for public-facing updates (e.g. GitHub issue comments) while '
            'keeping internal ticket details private.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'reply': {
                    'type': 'string',
                    'description': 'Public reply content to post on the linked source record.',
                },
                'reason': {
                    'type': 'string',
                    'description': 'Brief rationale for why this reply is appropriate.',
                },
            },
            'required': ['reply'],
        },
        'strict': True,
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Send a reply.'

    def get_suggestion_text(self, **kwargs):
        target = str((self.record_context or {}).get('original_source_type') or '').strip()
        if target == AgentSourceType.GITHUB_ISSUE.value:
            return f'Send a reply to the linked GitHub issue.'
        return self.suggestion_text

    def resolve_action_target(self, **kwargs) -> dict:
        target_source_type = str((self.record_context or {}).get('original_source_type') or '').strip()
        target_source_id = str((self.record_context or {}).get('original_source_id') or '').strip()
        target_source_title = str((self.record_context or {}).get('original_source_title') or '').strip()
        if not target_source_type or not target_source_id:
            return {}
        return {
            'source_type': target_source_type,
            'source_id': target_source_id,
            'source_title': target_source_title,
            # Reuse existing executor logic for source-specific reply suggestions.
            'tool_name': 'suggest_reply',
        }

    def execute(self, reply, reason=''):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        target_source_type = str((self.record_context or {}).get('original_source_type') or '').strip()
        target_source_id = str((self.record_context or {}).get('original_source_id') or '').strip()
        if not target_source_type or not target_source_id:
            return 'Cannot create linked-record reply suggestion: missing linked target.'
        reply = (reply or '').strip()
        if not reply:
            return 'Cannot create linked-record reply suggestion: empty reply.'

        preview = reply[:300]
        if len(reply) > 300:
            preview += '...'

        target_label = f'{target_source_type} {target_source_id}'.strip()
        return (
            f'Linked-record reply suggestion prepared from ticket #{source_id} - {source_title} '
            f'to {target_label}: {preview}'
        )


# All ticket tools for registration by TicketAgentWorker
TICKET_TOOL_CLASSES = [
    RecordTicketAnalysisTool,
    SuggestNotifyAssigneeTool,
    SuggestCloseTicketTool,
    SuggestLinkedRecordReplyTool,
]
