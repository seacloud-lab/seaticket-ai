"""
Common agent tools shared across source types.

Provides:
- BaseAgentTool: base class for tools that need record context
- FinalAnswerTool: signals the reasoning loop is complete
- SuggestReplyTool: suggest a user-facing reply
- SuggestCreateTicketTool: suggest creating an internal ticket
"""
import json
import logging
from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.utils.constants import AIScenario

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

    def get_project_uuid(self):
        """Return the current project UUID when available."""
        return (self.record_context or {}).get('project_uuid', '')

    def get_org_id(self):
        """Return the current org id when available."""
        return (self.record_context or {}).get('org_id', -1)

    def get_record_payload(self):
        """Return the structured record payload exposed to the agent."""
        record_payload = (self.record_context or {}).get('record_payload', {})
        return record_payload if isinstance(record_payload, dict) else {}

    def get_event_payload(self):
        """Return the triggering event payload exposed to the agent."""
        event_payload = (self.record_context or {}).get('event_payload', {})
        return event_payload if isinstance(event_payload, dict) else {}

    def get_app(self):
        """Return the runtime app when available."""
        return (self.record_context or {}).get('app')

    def get_suggestion_text(self, **kwargs):
        """Return the suggestion_text for this tool, optionally using call arguments."""
        return getattr(self, 'suggestion_text', '')


class RecordIssueAnalysisTool(BaseAgentTool):
    """Records structured analysis of the source record before handling actions."""

    tool = {
        'type': 'function',
        'name': 'record_issue_analysis',
        'description': (
            'Record your structured source record analysis after search and before handling tools. '
            'Include evidence gathered by searches, the recommended next action, '
            'and classification when the current scenario requires it.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'summary': {
                    'type': 'string',
                    'description': 'Your detailed analysis of the issue: what it is about, whether it resembles a known problem, why you came to this conclusion, and what action you plan to take. Cite search evidence with returned labels like <reference_0> whenever applicable. For multiple citations, place labels directly next to each other, like <reference_0><reference_1>; do not combine them inside one tag. Use straight quotes around any UI labels or feature names as needed; output the value as plain prose — do not additionally JSON-escape characters in your answer.'
                },
                'classified_issue_type': {
                    'type': 'string',
                    'enum': ['Bug', 'Feature', 'Question', 'Spam', 'Notification'],
                    'description': 'Optional agent-classified issue type based on title/content. Include it when the scenario requires issue classification.'
                },
                'original_issue_type': {
                    'type': 'string',
                    'description': 'The original issue_type value from input, used as a candidate label.'
                },
                'recommended_actions': {
                    'type': 'array',
                    'items': {
                        'type': 'string',
                        'enum': ['suggest_modify_type', 'suggest_assign_labels', 'suggest_reply', 'suggest_create_ticket', 'suggest_move_to_spam', 'no_op'],
                    },
                    'description': 'List of next action recommendations. Use ["no_op"] when no handling suggestion is needed.'
                },
                'relevant_reference_labels': {
                    'type': 'array',
                    'items': {
                        'type': 'string'
                    },
                    'description': 'The exact search-result labels that remain relevant to the final handling decision, for example ["<reference_0>", "<reference_3>"]. Use an empty array when no search result should be passed to handling.'
                }
            },
            'required': [
                'summary',
                'recommended_actions',
                'relevant_reference_labels'
            ]
        },
        'strict': True,
    }

    action_type = 'analysis'
    action_status = 'completed'

    def execute(
        self,
        summary,
        recommended_actions,
        classified_issue_type='',
        original_issue_type='',
        relevant_reference_labels=[]
    ):

        source_type = self.get_source_type()
        source_id = self.get_source_id()
        source_title = self.get_source_title()
        result = (
            f'Analysis recorded for {source_type} {source_id} - {source_title}. '
        )
        if classified_issue_type:
            result += f'Classified as {classified_issue_type}. '
        actions = recommended_actions if isinstance(recommended_actions, list) else []
        actions_display = ', '.join([str(action) for action in actions]) if actions else 'no_op'
        result += f'Recommended next actions: {actions_display}.'
        return result


class SuggestReplyTool(BaseAgentTool):
    """Suggests a direct reply.

    The suggestion is stored as pending and requires user confirmation before
    any action is taken (e.g. posting a reply on GitHub or closing the issue).
    """

    tool = {
        'type': 'function',
        'name': 'suggest_reply',
        'description': (
            'Suggest a reply for a source record that can be answered by search evidence '
            '(known issue, tracked request, or direct support answer). '
            'Include supporting URLs from relevant records whenever available. '
            'The suggestion requires user confirmation before being acted upon.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'reply': {
                    'type': 'string',
                    'description': (
                        'The proposed reply or answer to the issue. '
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
            'required': ['reply']
        },
        'strict': True,
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Send a reply'

    def execute(self, reply, reasoning=None):
        source_type = self.get_source_type()
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        reply = (reply or '').strip()
        if not reply:
            return 'Cannot create reply suggestion: empty reply content.'

        preview = reply[:300]
        if len(reply) > 300:
            preview += '...'

        return (
            f'Reply suggestion prepared for {source_type} {source_id} - {source_title}: {preview}'
        )


class SuggestCreateTicketTool(BaseAgentTool):
    """Suggests creating a ticket from a source record that represents new work."""

    tool = {
        'type': 'function',
        'name': 'suggest_create_ticket',
        'description': (
            'Suggest creating an internal ticket from this source record. '
            'Call this when search evidence is insufficient to resolve directly and the issue should be tracked. '
            'This action is usually pending for user confirmation, '
            'but some projects may configure it to auto-execute.'
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
        },
        'strict': True,
    }

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = 'Create an internal ticket from this record'

    @staticmethod
    def _format_ticket_suggestion_content(title, description):
        title = (title or '').strip() or 'Untitled'
        description = (description or '').strip() or 'No description provided.'
        return json.dumps({
            'title': title,
            'content': description,
        }, ensure_ascii=False)

    def _build_ticket_generation_detail(self, reason):
        detail = {
            'source_type': self.get_source_type(),
            'source_id': self.get_source_id(),
            'source_title': self.get_source_title(),
            'reason': (reason or '').strip(),
            'event': self.get_event_payload(),
            'record': self.get_record_payload(),
        }
        return json.dumps(detail, ensure_ascii=False, indent=2)

    def _generate_suggestion_content(self, reason):
        reason = (reason or '').strip()
        if not reason:
            return ''

        app = self.get_app()
        project_uuid = self.get_project_uuid()
        if app is None or not project_uuid:
            logger.warning(
                'Cannot generate ticket draft suggestion for %s %s: missing app or project_uuid.',
                self.get_source_type(),
                self.get_source_id(),
            )
            return ''

        context = {
            'org_id': self.get_org_id(),
            'project_uuid': project_uuid,
            'scenario': AIScenario.RECORD_GENERATION.value,
        }
        record_detail = self._build_ticket_generation_detail(reason)

        try:
            title, description = app.ai_services.gen_ticket_from_record(record_detail, context)
        except Exception as e:
            logger.warning(
                'Failed to generate ticket draft suggestion for %s %s: %s',
                self.get_source_type(),
                self.get_source_id(),
                e,
            )
            return ''

        title = (title or self.get_source_title() or '').strip()
        description = (description or '').strip()
        if not title and not description:
            return ''

        return self._format_ticket_suggestion_content(title, description)

    def execute(self, reason):
        source_type = self.get_source_type()
        source_id = self.get_source_id()
        source_title = self.get_source_title()

        reason = (reason or '').strip()
        if not reason:
            return 'Cannot create ticket suggestion: empty reason.'
        suggestion_content = self._generate_suggestion_content(reason)

        preview = reason[:300]
        if len(reason) > 300:
            preview += '...'

        return {
            'message': (
                f'Create-ticket suggestion prepared for {source_type} {source_id} - {source_title}. '
                f'Reason: {preview}'
            ),
            'suggestion_content': suggestion_content,
        }


class FinalAnswerTool(BasicTool):
    """Signals the end of the agent reasoning loop with a brief summary."""

    tool = {
        'type': 'function',
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
        },
        'strict': True,
    }

    action_type = 'summary'
    action_status = 'completed'

    def execute(self, summary=''):
        return summary or ''
