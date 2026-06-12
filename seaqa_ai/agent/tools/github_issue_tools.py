"""
Agent tools specific to GitHub issue processing.

The agent uses this flow for each GitHub issue:
  1. record_issue_analysis     — record analysis before handling.
  2. suggest_modify_type       — optional, user-facing type suggestion.
  3a. suggest_reply            — if the issue can be resolved with known info.
  3b. suggest_create_ticket    — if the issue should become a tracked ticket.
"""
import json
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
        },
        'strict': True,
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


class SuggestAssignLabelsTool(BaseAgentTool):
    """Suggests updating GitHub issue labels."""

    tool = {
        'type': 'function',
        'name': 'suggest_assign_labels',
        'description': (
            'Suggest labels for this GitHub issue. '
            'Only choose from available_labels in input and avoid inventing labels.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'current_labels': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Current labels on the GitHub issue.',
                },
                'suggested_labels': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'Suggested labels picked from available_labels.',
                },
                'reason': {
                    'type': 'string',
                    'description': 'Explain why these labels are suitable for this issue.',
                },
            },
            'required': ['suggested_labels', 'reason'],
        },
        'strict': True,
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Suggest assigning labels to this GitHub issue.'

    def get_suggestion_text(self, **kwargs):
        labels = kwargs.get('suggested_labels') or []
        if not isinstance(labels, list):
            labels = []
        normalized = []
        for label in labels:
            name = str(label).strip()
            if name:
                normalized.append(name)
        serialized = json.dumps(normalized, ensure_ascii=True)
        return f'Suggest assigning labels {serialized} to this GitHub issue.'

    def execute(self, suggested_labels, reason, current_labels=None):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        labels = suggested_labels if isinstance(suggested_labels, list) else []
        normalized = []
        for label in labels:
            name = str(label).strip()
            if name:
                normalized.append(name)
        if not normalized:
            return 'Cannot create assign-labels suggestion: empty suggested_labels.'

        reason = (reason or '').strip()
        if not reason:
            return 'Cannot create assign-labels suggestion: empty reason.'

        serialized = json.dumps(normalized, ensure_ascii=True)
        return (
            f'Label suggestion prepared for GitHub issue {source_id} - {source_title}. '
            f'Suggested labels: {serialized}; Reason: {reason}'
        )


# All GitHub issue tools for registration by GithubIssueAgentWorker
GITHUB_ISSUE_TOOL_CLASSES = [
    RecordIssueAnalysisTool,
    SuggestModifyTypeTool,
    SuggestAssignLabelsTool,
    SuggestReplyTool,
    SuggestCreateTicketTool,
]
