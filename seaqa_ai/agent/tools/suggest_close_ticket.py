from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestCloseTicket(BasicAgentSuggestionTool):
    suggestion_text = 'Close this ticket'
    tool = {
        'type': 'function', 'name': 'suggest_close_ticket',
        'description': 'Create a pending suggestion to close the current ticket.',
        'parameters': {
            'type': 'object',
            'properties': {'reason': {'type': 'string'}, 'rule': {'type': 'string'}},
            'required': ['reason'], 'additionalProperties': False,
        }, 'strict': True,
    }

    def execute(self, reason, rule=''):
        reason = str(reason or '').strip()
        if not reason:
            raise ValueError('reason must not be empty')
        return {'status': 'pending', 'suggestion_content': reason}
