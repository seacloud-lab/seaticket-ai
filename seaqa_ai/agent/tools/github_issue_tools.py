"""
Agent tools specific to GitHub issue processing.

The agent uses this flow for each GitHub issue:
  1. record_issue_analysis     — record analysis before handling.
  2. suggest_modify_type       — optional, user-facing type suggestion.
  3a. suggest_reply            — if the issue can be resolved with known info.
  3b. suggest_create_ticket    — if the issue should become a tracked ticket.
"""
import logging
from seaqa_ai.agent.tools.common_tools import (
    BaseAgentTool,
    RecordIssueAnalysisTool,
    SuggestCreateTicketTool,
    SuggestReplyTool,
)

logger = logging.getLogger(__name__)


class SuggestModifyTypeTool(BaseAgentTool):
    """Suggests a user-facing issue type update."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_modify_type',
            'description': (
                'Suggest that the user update how this issue is categorized '
                '(Bug, Feature, or Question). '
                'Only call this when you can clearly explain why the current type is less suitable.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'current_issue_type': {
                        'type': 'string',
                        'description': 'The current issue_type label from input.'
                    },
                    'suggested_type': {
                        'type': 'string',
                        'enum': ['Bug', 'Feature', 'Question'],
                        'description': 'Your suggested user-facing type for this issue.'
                    },
                    'reason': {
                        'type': 'string',
                        'description': (
                            'Explain why the current issue type is less appropriate and '
                            'why the suggested type is a better fit. Additionally, you should clearly specify the suggested type.'
                        )
                    }
                },
                'required': ['current_issue_type', 'suggested_type', 'reason']
            }
        }
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Suggest a more suitable issue type for this GitHub issue.'

    def get_suggestion_text(self, **kwargs):
        current = kwargs.get('current_issue_type', '')
        suggested = kwargs.get('suggested_type', '')
        if current:
            if current == 'null' or current == 'Null' or current == 'NULL' or current == 'None' or current == 'none' or current == 'NONE':
                return f'Suggest changing the type to "{suggested}" for this GitHub issue.'
            return f'Suggest changing the type from "{current}" to "{suggested}" for this GitHub issue.'
        else:
            return f'Suggest changing the type to "{suggested}" for this GitHub issue.'

    def execute(self, current_issue_type, suggested_type, reason):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        reason = (reason or '').strip()
        if not reason:
            return 'Cannot create modify-type suggestion: empty reason.'

        preview = reason[:300]
        if len(reason) > 300:
            preview += '...'

        return (
            f'Issue type suggestion prepared for GitHub issue {source_id} - {source_title}. '
            f'Current: {current_issue_type}; Suggested: {suggested_type}; '
            f'Reason: {preview}'
        )


# All GitHub issue tools for registration by GithubIssueAgentWorker
GITHUB_ISSUE_TOOL_CLASSES = [
    RecordIssueAnalysisTool,
    SuggestModifyTypeTool,
    SuggestReplyTool,
    SuggestCreateTicketTool,
]
