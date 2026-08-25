from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils.record_identity import record_identity_schema


class SuggestReplyExternalTargets(BasicAgentSuggestionTool):
    tool = {
        'type': 'function',
        'name': 'suggest_reply_external_targets',
        'description': 'Create a pending public reply suggestion for linked external records.',
        'parameters': {
            'type': 'object',
            'properties': {
                'reply': {'type': 'string'},
                'reason': {'type': 'string'},
                'reply_targets': {
                    'type': 'array',
                    'items': record_identity_schema(nested=True),
                    'minItems': 1,
                    'description': 'Linked external records that should receive the reply.',
                },
            },
            'required': ['reply', 'reason', 'reply_targets'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, reply, reason, reply_targets, context=None):
        reply = str(reply or '').strip()
        reason = str(reason or '').strip()
        if not reply or not reason:
            raise ValueError('reply and reason must not be empty')
        if not isinstance(reply_targets, list):
            raise ValueError('reply_targets must be an array')

        targets = self._linked_external_targets(reply_targets, context)
        if not targets:
            raise ValueError('no external reply targets are available')
        return self.suggestion_result(
            suggestion_content=reply,
            targets=targets,
            suggestion_reason=reason,
            tool_name='suggest_reply',
        )
