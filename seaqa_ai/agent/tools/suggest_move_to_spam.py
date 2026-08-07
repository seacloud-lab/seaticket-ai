from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestMoveToSpam(BasicAgentSuggestionTool):
    suggestion_text = 'Move this email thread to the spam folder'
    tool = {
        'type': 'function', 'name': 'suggest_move_to_spam',
        'description': 'Create a pending suggestion to move an email thread to spam.',
        'parameters': {
            'type': 'object', 'properties': {'reason': {'type': 'string'}},
            'required': ['reason'], 'additionalProperties': False,
        }, 'strict': True,
    }

    def execute(self, reason, context=None):
        if not str(reason or '').strip():
            raise ValueError('reason must not be empty')
        return self.suggestion_result(context=context)
