from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestReply(BasicAgentSuggestionTool):
    suggestion_text = 'Send a reply'
    tool = {
        'type': 'function', 'name': 'suggest_reply',
        'description': 'Create a pending suggestion to send a user-facing reply.',
        'parameters': {
            'type': 'object',
            'properties': {
                'reply': {'type': 'string'},
                'reason': {'type': 'string'},
            },
            'required': ['reply', 'reason'], 'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, reply, reason, context=None):
        reply = str(reply or '').strip()
        if not reply:
            raise ValueError('reply must not be empty')
        return {'status': 'pending', 'suggestion_content': reply}
