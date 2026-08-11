"""
Agent tools specific to email thread processing.

The agent uses this flow for each email thread:
  1. record_issue_analysis     — record analysis before handling.
  2a. suggest_reply            — if the thread can be answered by known info.
  2b. suggest_create_ticket    — if the thread should become a tracked ticket.
  2c. suggest_move_to_spam     — if the thread is identified as spam/marketing.
"""
from seaqa_ai.agent.tools.common_tools import (
    BaseAgentTool,
    RecordIssueAnalysisTool,
    SuggestCreateTicketTool,
    SuggestReplyTool,
)


class SuggestMoveToSpamTool(BaseAgentTool):
    """Suggests moving a spam email thread into the spam mailbox."""

    tool = {
        'type': 'function',
        'name': 'suggest_move_to_spam',
        'description': (
            'Suggest moving this email thread to the spam/junk mailbox '
            'when it is identified as spam or marketing email. '
            'The action requires user confirmation before execution.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'reason': {
                    'type': 'string',
                    'description': (
                        'Why this email thread is considered spam/marketing '
                        'and should be moved to spam.'
                    ),
                }
            },
            'required': ['reason'],
        },
        'strict': True,
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Move this email thread to the spam folder'

    def execute(self, reason):
        source_type = self.get_source_type()
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        reason = (reason or '').strip()
        if not reason:
            return 'Cannot create spam-move suggestion: empty reason.'

        preview = reason[:300]
        if len(reason) > 300:
            preview += '...'

        return (
            f'Spam-move suggestion prepared for {source_type} {source_id} - '
            f'{source_title}. Reason: {preview}'
        )


EMAIL_TOOL_CLASSES = [
    RecordIssueAnalysisTool,
    SuggestReplyTool,
    SuggestCreateTicketTool,
    SuggestMoveToSpamTool,
]
