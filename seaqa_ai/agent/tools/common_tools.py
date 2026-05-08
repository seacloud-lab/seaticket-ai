"""
Common agent tools shared across source types.

Provides:
- BaseAgentTool: base class for tools that need record context
- RecordEventsSummaryTool: records a concise summary of trigger events
- FinalAnswerTool: signals the reasoning loop is complete
- SuggestResolutionTool: suggest a user-facing resolution
- SuggestCreateTicketTool: suggest creating an internal ticket
"""
import logging
from seaqa_ai.utils.tools import BasicTool

logger = logging.getLogger(__name__)


class BaseAgentTool(BasicTool):
    """Base class for agent tools that need record context.

    Subclasses are injected with a record_context dict before execution so they
    know which source record (ticket, github issue, etc.) they are acting on.
    """

    record_context = None

    def set_record_context(self, record_context):
        """Inject the record context from the worker before tool execution."""
        self.record_context = record_context or {}

    def get_source_type(self):
        """Return the source type string (e.g. 'ticket', 'github_issue')."""
        return (self.record_context or {}).get('source_type', 'unknown')

    def get_source_id(self):
        """Return the source_id string (ticket pk or '{connection_id}_{record_id}')."""
        return (self.record_context or {}).get('source_id', 'unknown')

    def get_source_title(self):
        """Return the human-readable title of the source record."""
        return (self.record_context or {}).get('source_title', 'Untitled')

    def get_suggestion_text(self, **kwargs):
        """Return the suggestion_text for this tool, optionally using call arguments."""
        return getattr(self, 'suggestion_text', '')


class RecordEventsSummaryTool(BaseAgentTool):
    """Records a concise summary of the trigger events for this run."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'record_events_summary',
            'description': (
                'Record a concise summary of the trigger events for this run. '
                'Summarize only the input events that triggered this run: what type of event, what changed. '
                'Be concise and avoid repeating the full record content.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'summary': {
                        'type': 'string',
                        'description': (
                            'A concise summary of the trigger events and their likely impact on this run. '
                            'Mention the most important changes only.'
                        ),
                    }
                },
                'required': ['summary'],
            },
        },
    }

    action_type = 'events'
    action_status = 'completed'

    def execute(self, summary):
        source_type = self.get_source_type()
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        summary = (summary or '').strip()
        if not summary:
            return 'Cannot record event summary: empty summary.'

        return (
            f'Event summary recorded for {source_type} {source_id} - {source_title}: {summary}'
        )


class RecordIssueAnalysisTool(BaseAgentTool):
    """Records structured analysis of the source record before handling actions."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'record_issue_analysis',
            'description': (
                'Record your structured source record analysis after search and before handling tools. '
                'Include classification, evidence got by searches, and recommended next action.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'summary': {
                        'type': 'string',
                        'description': 'Your detailed analysis of the issue: what it is about, whether it resembles a known problem, why you came to this conclusion, and what action you plan to take. Use straight quotes around any UI labels or feature names as needed; output the value as plain prose — do not additionally JSON-escape characters in your answer.'
                    },
                    'classified_issue_type': {
                        'type': 'string',
                        'enum': ['Bug', 'Feature', 'Question'],
                        'description': 'The agent-classified issue type based on title/content.'
                    },
                    'original_issue_type': {
                        'type': 'string',
                        'description': 'The original issue_type value from input, used as a candidate label.'
                    },
                    'recommended_action': {
                        'type': 'string',
                        'enum': ['suggest_modify_type', 'suggest_resolution', 'suggest_create_ticket'],
                        'description': 'The handling action to call next.'
                    },
                },
                'required': [
                    'summary',
                    'classified_issue_type',
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
        recommended_action='suggest_create_ticket',
    ):

        source_type = self.get_source_type()
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        return (
            f'Analysis recorded for {source_type} {source_id} - {source_title}. '
            f'Classified as {classified_issue_type}. '
            f'Recommended next action: {recommended_action}.'
        )


class SuggestResolutionTool(BaseAgentTool):
    """Suggests a resolution or direct reply for a source record.

    The suggestion is stored as pending and requires user confirmation before
    any action is taken (e.g. posting a reply on GitHub or closing the issue).
    """

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_resolution',
            'description': (
                'Suggest a resolution/reply for a source record that can be answered by search evidence '
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
    suggestion_text = 'Propose a resolution.'

    def execute(self, resolution, reasoning=None):
        source_type = self.get_source_type()
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        resolution = (resolution or '').strip()
        if not resolution:
            return 'Cannot create resolution suggestion: empty resolution content.'

        preview = resolution[:300]
        if len(resolution) > 300:
            preview += '...'

        return (
            f'Resolution suggestion prepared for {source_type} {source_id} - {source_title}: {preview}'
        )


class SuggestCreateTicketTool(BaseAgentTool):
    """Suggests creating a ticket from a source record that represents new work."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'suggest_create_ticket',
            'description': (
                'Suggest creating an internal ticket from this source record. '
                'Call this when search evidence is insufficient to resolve directly and the issue should be tracked. '
                'The ticket creation requires user confirmation.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'reason': {
                        'type': 'string',
                        'description': 'Explain why this record should become an internal ticket.'
                    }
                },
                'required': ['reason']
            }
        }
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Create an internal ticket from this record.'

    def execute(self, reason):
        source_type = self.get_source_type()
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        reason = (reason or '').strip()
        if not reason:
            return 'Cannot create ticket suggestion: empty reason.'

        preview = reason[:300]
        if len(reason) > 300:
            preview += '...'

        return (
            f'Create-ticket suggestion prepared for {source_type} {source_id} - {source_title}. '
            f'Reason: {preview}'
        )


class ThinkTool(BasicTool):
    """No-op tool that gives the model a structured space to reason.

    The model calls this tool to pause and think before deciding the next
    action. Inspired by Anthropic's "think" tool pattern (https://www.anthropic.com/engineering/claude-think-tool).
    """

    tool = {
        'type': 'function',
        'function': {
            'name': 'think',
            'description': (
                'Use this tool to think about the current situation. It will not obtain '
                'new information or change anything, but just append the thought '
                'to the log. Use it when complex reasoning or analysis of tool '
                'results is needed before deciding next steps. '
                'Keep your thought concise: at most 3-8 bullet points or a short paragraph (under 1000 words). '
                'Do NOT repeat raw data or search results verbatim.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'thought': {
                        'type': 'string',
                        'description': (
                            'Your concise reasoning or analysis in under 1000 words. '
                            'Use short bullet points. Do not copy-paste raw data.'
                        ),
                    }
                },
                'required': ['thought'],
            },
        },
    }

    action_type = 'thought'
    action_status = 'completed'

    def execute(self, thought: str = ''):
        return ''


class FinalAnswerTool(BasicTool):
    """Signals the end of the agent reasoning loop with a brief summary."""

    tool = {
        'type': 'function',
        'function': {
            'name': 'final_answer',
            'description': 'Provide the final answer/summary when you have completed all tasks.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'summary': {
                        'type': 'string',
                        'description': 'A brief summary of what was done.'
                    }
                }
            }
        }
    }

    action_type = 'summary'
    action_status = 'completed'

    def execute(self, summary=''):
        return summary or ''
