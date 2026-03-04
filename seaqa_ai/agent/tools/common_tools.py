"""
Common agent tool infrastructure shared across all source types.

Provides:
- BaseAgentTool: base class for tools that need record context (source_type, source_id, source_title)
- FinalAnswerTool: signals the agent has completed its reasoning loop
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
                    'answer': {
                        'type': 'string',
                        'description': 'The final answer or summary of actions taken.'
                    },
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

    def execute(self, answer='', summary=''):
        return answer or summary or ''
