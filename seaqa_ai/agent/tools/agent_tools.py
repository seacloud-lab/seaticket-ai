import logging
from seaqa_ai.utils.tools import BasicTool

logger = logging.getLogger(__name__)


class TicketAgentTool(BasicTool):
    """Base class for agent tools that need ticket context."""
    
    # Ticket context will be set by the agent before execution
    ticket_context = None
    
    def set_ticket_context(self, ticket_context):
        """Set the ticket context for this tool."""
        self.ticket_context = ticket_context or {}
    
    def get_ticket_id(self):
        """Get the current ticket ID from context."""
        return (self.ticket_context or {}).get('ticket_id', 'Unknown ID')
    
    def get_ticket_title(self):
        """Get the current ticket title from context."""
        return (self.ticket_context or {}).get('ticket_title', 'Untitled')


class AnalyzeTicketTool(TicketAgentTool):
    """Tool for the agent to explicitly record its analysis of a ticket."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'analyze_ticket',
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
                        'description': 'Your analysis of the ticket: current state, issues found, and what action you plan to take.'
                    }
                },
                'required': ['analysis']
            }
        }
    }

    action_type = 'analysis'
    action_status = 'completed'

    def execute(self, analysis):
        ticket_id = self.get_ticket_id()
        ticket_title = self.get_ticket_title()
        return f'Analysis recorded for ticket #{ticket_id} - {ticket_title}.'


class NotifyAssigneeTool(TicketAgentTool):
    """Tool to notify assignees of a ticket."""
    
    tool = {
        'type': 'function',
        'function': {
            'name': 'notify_assignee',
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

    # Metadata for action recording
    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Suggest notifying assignees of the ticket.'

    def execute(self, message, reason=None):
        """
        Notify assignees of a ticket automatically.
        (seaqa-web handles this through its ProjectNotification system).
        
        Args:
            message: The notification message
            reason: The reason for notification
        
        Returns:
            str: Result description
        """
        ticket_id = self.get_ticket_id()
        ticket_title = self.get_ticket_title()
        
        message = (message or '').strip()
        reason = (reason or 'unspecified').strip()

        if not message:
            return 'Cannot notify assignee: empty message content.'

        preview = message[:300]
        if len(message) > 300:
            preview += '...'

        result_text = (
            f'Notification suggestion prepared for ticket #{ticket_id} - {ticket_title} '
            f'(reason: {reason}): {preview}'
        )
        return result_text


class AddCommentTool(TicketAgentTool):
    """Tool to suggest adding a comment to a ticket."""
    
    tool = {
        'type': 'function',
        'function': {
            'name': 'add_comment',
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

    # Metadata for action recording
    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Suggest adding a comment to the ticket.'

    def execute(self, comment):
        """
        Suggest adding a comment to a ticket. The comment is not posted
        immediately but stored as a pending suggestion for user review.
        
        Args:
            comment: The comment content
        
        Returns:
            str: Result description
        """
        ticket_id = self.get_ticket_id()
        ticket_title = self.get_ticket_title()
        
        comment = (comment or '').strip()

        if not comment:
            return 'Cannot add comment suggestion: empty comment content.'

        preview = comment[:300]
        if len(comment) > 300:
            preview += '...'

        result_text = f'Comment suggestion prepared for ticket #{ticket_id} - {ticket_title}: {preview}'
        return result_text


# Collect all agent tools for easy registration
AGENT_TOOL_CLASSES = [
    AnalyzeTicketTool,
    NotifyAssigneeTool,
    AddCommentTool,
]
