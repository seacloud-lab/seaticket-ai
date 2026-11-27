from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestReply(BasicAgentSuggestionTool):
    editable_content_mode = 'text'
    tool = {
        'type': 'function',
        'name': 'suggest_reply',
        'description': 'Create a pending suggestion to send a user-facing reply.',
        'parameters': {
            'type': 'object',
            'properties': {
                'reply': {'type': 'string'},
                'reason': {'type': 'string'},
                'reply_to': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': (
                        'Optional email reply recipients. Provide only for email targets when '
                        'replying to the original sender of a forwarded email or when explicitly '
                        'requested during regeneration.'
                    ),
                },
            },
            'required': ['reply', 'reason'],
            'additionalProperties': False,
        },
        'strict': False,
    }

    def execute(self, reply, reason, reply_to=None, context=None):
        reply = str(reply or '').strip()
        if not reply:
            raise ValueError('reply must not be empty')
        target = self._target_from_trigger_context(context)
        normalized_reply_to = self._normalize_email_reply_to(reply_to, target, context)
        suggestion_payload = {'reply_to': normalized_reply_to} if normalized_reply_to else {}
        return self.suggestion_result(
            suggestion_content=reply,
            context=context,
            targets=[target],
            suggestion_reason=reason,
            suggestion_payload=suggestion_payload,
        )
