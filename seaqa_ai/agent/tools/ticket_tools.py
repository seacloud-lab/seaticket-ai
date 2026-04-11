"""
Agent tools specific to ticket processing.

Tools:
- AnalyzeTicketTool: records the agent's analysis of a ticket
- NotifyAssigneeTool: suggests notifying ticket assignees (pending user confirmation)
- AddCommentTool: suggests adding a comment to a ticket (pending user confirmation)
"""
import logging
from seaqa_ai.agent.tools.common_tools import BaseAgentTool

logger = logging.getLogger(__name__)


class RecordTicketAnalysisTool(BaseAgentTool):
    """Records the agent's analysis of a ticket before taking any action."""

    tool = {
        'type': 'function',
        'function': {
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
                        'description': 'Your detailed analysis of the ticket: current state, issues found, and what action you plan to take.'
                    }
                },
                'required': ['analysis']
            }
        }
    }

    action_type = 'analysis'
    action_status = 'completed'

    def execute(self, analysis):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        return f'Analysis recorded for ticket #{source_id} - {source_title}.'


class SuggestNotifyAssigneeTool(BaseAgentTool):
    """Suggests notifying the assignees of a ticket; requires user confirmation."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_notify_assignee',
            'description': (
                'Send a notification to the assignees of a ticket. '
                'Use this when a ticket is approaching its due date, '
                'has been inactive for too long, or needs attention. '
                'It needs user confirmation. Thus the action status should be pending.'
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
            }
        }
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


class SuggestAddCommentTool(BaseAgentTool):
    """Suggests adding a comment to a ticket; requires user confirmation."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_add_comment',
            'description': (
                'Add a comment to a ticket to reply the messages in the ticket. '
                'The comment will be marked as pending '
                'and requires user confirmation before being actually posted. '
                'Use this to suggest responses or updates on tickets.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'comment': {
                        'type': 'string',
                        'description': 'The comment to add to the ticket. This should not be empty.'
                    }
                },
                'required': ['comment']
            }
        }
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Add a comment to the ticket.'

    def execute(self, comment):
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        comment = (comment or '').strip()
        if not comment:
            return 'Cannot add comment suggestion: empty comment content.'

        preview = comment[:300]
        if len(comment) > 300:
            preview += '...'

        return f'Comment suggestion prepared for ticket #{source_id} - {source_title}: {preview}'


# All ticket-specific tools for registration by TicketAgentWorker
TICKET_TOOL_CLASSES = [
    RecordTicketAnalysisTool,
    SuggestNotifyAssigneeTool,
    SuggestAddCommentTool,
]
