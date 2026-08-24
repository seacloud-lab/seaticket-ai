from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils.record_identity import record_identity_schema


class SuggestCloseTicket(BasicAgentSuggestionTool):
    suggestion_text = 'Close this ticket'
    tool = {
        'type': 'function',
        'name': 'suggest_close_ticket',
        'description': 'Create a pending suggestion to close the current or specified ticket.',
        'parameters': {
            'type': 'object',
            'properties': {
                'reason': {'type': 'string'},
                'rule': {'type': 'string'},
                'target': {
                    **record_identity_schema(nested=True),
                },
            },
            'required': ['reason'],
            'additionalProperties': False,
        },
        'strict': False,
    }

    def execute(self, reason, rule='', target=None, context=None):
        reason = str(reason or '').strip()
        rule = str(rule or '').strip()
        if not reason:
            raise ValueError('reason must not be empty')
        if target is None and (context or {}).get('source_type') != 'ticket':
            raise ValueError('suggest_close_ticket target is required outside a ticket task')
        target = self._loaded_target(target, context, 'ticket')
        return self.suggestion_result(
            targets=[target],
            suggestion_reason=f'{reason} Rule: {rule}' if rule else reason,
        )
