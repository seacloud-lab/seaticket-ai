from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestReply(BasicAgentSuggestionTool):
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
        return self.suggestion_result(
            suggestion_content=reply,
            context=context,
            targets=[self._target_from_trigger_context(context)],
            suggestion_reason=reason,
        )
