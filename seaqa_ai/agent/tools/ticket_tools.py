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

logger = logging.getLogger(__name__)


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
                'recommended_action': {
                    'type': 'string',
                    'enum': ['suggest_notify_assignee', 'no_op'],
                    'description': 'The recommended action to take next.'
                }
            },
            'required': ['analysis', 'recommended_action']
        },
        'strict': False,
    }

    action_type = 'analysis'
    action_status = 'completed'

    def execute(self, analysis, recommended_action):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        return f'Analysis recorded for ticket #{source_id} - {source_title}. Recommended action: {recommended_action}.'


class SuggestNotifyAssigneeTool(BaseAgentTool):
    """Suggests notifying the assignees of a ticket; requires user confirmation."""

    tool = {
        'type': 'function',
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
        },
        'strict': False,
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


# All ticket tools for registration by TicketAgentWorker
TICKET_TOOL_CLASSES = [
    RecordTicketAnalysisTool,
    SuggestNotifyAssigneeTool,
]
