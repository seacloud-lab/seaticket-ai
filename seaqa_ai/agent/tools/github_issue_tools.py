"""
Agent tools specific to GitHub Issue processing.

The agent uses this flow for each GitHub issue:
  1. record_issue_analysis  — called after search, before handling.
  2. suggest_modify_type    — optional, user-facing type suggestion.
  3a. suggest_resolution    — if the issue can be resolved with known info.
  3b. suggest_create_ticket — if the issue should become a tracked ticket.
"""
import logging
from seaqa_ai.agent.tools.common_tools import BaseAgentTool

logger = logging.getLogger(__name__)


class RecordIssueAnalysisTool(BaseAgentTool):
    """Records structured analysis of a GitHub issue before handling actions."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'record_issue_analysis',
            'description': (
                'Record your structured GitHub issue analysis after search and before handling tools. '
                'Include classification, evidence got by searches, and recommended next action.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'summary': {
                        'type': 'string',
                        'description': 'Your analysis of the issue: what it is about, whether it resembles a known problem, and what action you plan to take.'
                    },
                    'classified_issue_type': {
                        'type': 'string',
                        'enum': ['bug report', 'feature request', 'general support question'],
                        'description': 'The agent-classified issue type based on title/content.'
                    },
                    'original_issue_type': {
                        'type': 'string',
                        'description': 'The original issue_type value from input, used as a candidate label.'
                    },
                    'searches_performed': {
                        'type': 'array',
                        'description': 'Searches performed before this analysis.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'tool': {
                                    'type': 'string',
                                    'enum': ['documents_search', 'issues_search']
                                },
                                'query': {
                                    'type': 'string'
                                },
                                'result_count': {
                                    'type': 'integer'
                                },
                                'notes': {
                                    'type': 'string'
                                }
                            },
                            'required': ['tool', 'query', 'result_count']
                        }
                    },
                    'key_evidence': {
                        'type': 'string',
                        'description': 'Key evidence from search results that supports the decision.'
                    },
                    'can_answer_directly': {
                        'type': 'boolean',
                        'description': 'Whether search results are sufficient to directly answer the issue.'
                    },
                    'recommended_action': {
                        'type': 'string',
                        'enum': ['suggest_modify_type', 'suggest_resolution', 'suggest_create_ticket'],
                        'description': 'The handling action to call next.'
                    },
                    'rationale': {
                        'type': 'string',
                        'description': 'Brief reasoning that links evidence to recommended_action.'
                    },
                },
                'required': [
                    'summary',
                    'classified_issue_type',
                    'searches_performed',
                    'key_evidence',
                    'can_answer_directly',
                    'recommended_action'
                ]
            }
        }
    }

    action_type = 'analysis'
    action_status = 'completed'

    def execute(
        self,
        summary,
        classified_issue_type,
        original_issue_type='',
        searches_performed=None,
        key_evidence='',
        can_answer_directly=False,
        recommended_action='suggest_create_ticket',
        rationale='',
    ):
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        return (
            f'Analysis recorded for GitHub issue {source_id} - {source_title}. '
            f'Classified as {classified_issue_type}. '
            f'Recommended next action: {recommended_action}.'
        )


class SuggestModifyTypeTool(BaseAgentTool):
    """Suggests a user-facing issue type update."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_modify_type',
            'description': (
                'Suggest that the user update how this issue is categorized '
                '(bug report, feature request, or general support question). '
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
                        'enum': ['bug report', 'feature request', 'general support question'],
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


class SuggestResolutionTool(BaseAgentTool):
    """Suggests a resolution or direct reply for a GitHub issue.

    The suggestion is stored as pending and requires user confirmation before
    any action is taken (e.g. posting a reply on GitHub or closing the issue).
    """

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_resolution',
            'description': (
                'Suggest a resolution/reply for a GitHub issue that can be answered by search evidence '
                '(known issue, tracked request, or direct support answer). '
                'Include supporting URLs from relevant records whenever available. '
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
                            'For known/tracked cases, include URLs to the supporting records '
                            '(GitHub issue, forum topic, email thread, ticket, or docs).'
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
                'Call this when search evidence is insufficient to resolve directly and the issue should be tracked '
                '(e.g. new bug, new feature request, or unresolved support problem). '
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
    RecordIssueAnalysisTool,
    SuggestModifyTypeTool,
    SuggestResolutionTool,
    SuggestCreateTicketTool,
]
