"""
Agent tools specific to GitHub Issue processing.

The agent follows a two-step decision flow for each new issue:
  1. analyze_issue  — always called first to record findings
  2a. suggest_resolution   — if the issue matches a known problem (pending user confirmation)
  2b. suggest_create_ticket — if the issue is novel and should become a ticket (pending user confirmation)

Only one of 2a/2b should be called per issue run.
"""
import logging
from seaqa_ai.agent.tools.common_tools import BaseAgentTool

logger = logging.getLogger(__name__)


class AnalyzeIssueTool(BaseAgentTool):
    """Records the agent's analysis of a GitHub issue before taking any action."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'analyze_issue',
            'description': (
                'Record your analysis of the GitHub issue. '
                'Always call this tool first before taking any other actions. '
                'Assess whether this issue describes a known/documented problem '
                'or is a genuinely new issue that warrants creating a ticket.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'analysis': {
                        'type': 'string',
                        'description': (
                            'Your analysis of the issue: what it is about, '
                            'whether it resembles a known problem, and what action you plan to take.'
                        )
                    },
                    'is_known_issue': {
                        'type': 'boolean',
                        'description': (
                            'True if this issue matches a known/documented problem that can be resolved '
                            'with existing knowledge. False if it is a new problem requiring a ticket.'
                        )
                    }
                },
                'required': ['analysis', 'is_known_issue']
            }
        }
    }

    action_type = 'analysis'
    action_status = 'completed'

    def execute(self, analysis, is_known_issue=False):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        verdict = 'known issue' if is_known_issue else 'new issue'
        return (
            f'Analysis recorded for GitHub issue {source_id} - {source_title}. '
            f'Verdict: {verdict}.'
        )


class SuggestResolutionTool(BaseAgentTool):
    """Suggests a resolution for a GitHub issue that matches a known problem.

    The suggestion is stored as pending and requires user confirmation before
    any action is taken (e.g. posting a reply on GitHub or closing the issue).
    """

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_resolution',
            'description': (
                'Suggest a resolution for a GitHub issue that can be addressed with existing knowledge. '
                'Call this when the issue matches a known problem. '
                'The resolution MUST include the URL of the related record (GitHub issue or forum topic) '
                'so the user can refer to it. '
                'The suggestion requires user confirmation before being acted upon.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'resolution': {
                        'type': 'string',
                        'description': (
                            'The proposed resolution or answer to the issue. '
                            'Write it as if it were a helpful reply to the issue author. '
                            'MUST include the URL of the related record (e.g. the duplicate GitHub issue '
                            'or the Discourse forum topic) so the user can refer to it directly.'
                        )
                    },
                    'reasoning': {
                        'type': 'string',
                        'description': 'Brief explanation of why this issue is considered a known problem.'
                    }
                },
                'required': ['resolution']
            }
        }
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Propose a resolution for this GitHub issue.'

    def execute(self, resolution, reasoning=None):
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        resolution = (resolution or '').strip()
        if not resolution:
            return 'Cannot create resolution suggestion: empty resolution content.'

        preview = resolution[:300]
        if len(resolution) > 300:
            preview += '...'

        return (
            f'Resolution suggestion prepared for GitHub issue {source_id} - {source_title}: {preview}'
        )


class SuggestCreateTicketTool(BaseAgentTool):
    """Suggests creating a ticket from a GitHub issue that represents a new problem.

    The suggestion is stored as pending. When the user confirms, seaqa-web will
    call the AI to generate ticket content and create the ticket, linking it back
    to the source GitHub issue via linked_connection_records.
    """

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_create_ticket',
            'description': (
                'Suggest creating an internal ticket from this GitHub issue. '
                'Call this when the issue represents a new problem not covered by existing knowledge. '
                'The ticket creation requires user confirmation.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'reason': {
                        'type': 'string',
                        'description': (
                            'Explain why this issue should become a ticket '
                            '(e.g. new bug, feature request not yet tracked).'
                        )
                    }
                },
                'required': ['reason']
            }
        }
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Create an internal ticket from this GitHub issue.'

    def execute(self, reason):
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        reason = (reason or '').strip()
        if not reason:
            return 'Cannot create ticket suggestion: empty reason.'

        preview = reason[:300]
        if len(reason) > 300:
            preview += '...'

        return (
            f'Create-ticket suggestion prepared for GitHub issue {source_id} - {source_title}. '
            f'Reason: {preview}'
        )


# All GitHub issue tools for registration by GithubIssueAgentWorker
GITHUB_ISSUE_TOOL_CLASSES = [
    AnalyzeIssueTool,
    SuggestResolutionTool,
    SuggestCreateTicketTool,
]
