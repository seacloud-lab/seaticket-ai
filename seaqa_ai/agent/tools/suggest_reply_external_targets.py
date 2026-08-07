from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestReplyExternalTargets(BasicAgentSuggestionTool):
    suggestion_text = 'Send a reply'
    tool = {
        'type': 'function', 'name': 'suggest_reply_external_targets',
        'description': 'Create pending public reply suggestions for system-provided external targets.',
        'parameters': {
            'type': 'object',
            'properties': {
                'reply': {'type': 'string'},
                'reason': {'type': 'string'},
                'targets': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'source_type': {'type': 'string'},
                            'connection_id': {'type': ['integer', 'null']},
                            'record_id': {'type': 'integer'},
                            'source_title': {'type': 'string'},
                        },
                        'required': ['source_type', 'connection_id', 'record_id'],
                        'additionalProperties': False,
                    },
                },
            },
            'required': ['reply', 'reason'], 'additionalProperties': False,
        }, 'strict': True,
    }

    def execute(self, reply, reason, targets=None, context=None):
        if not str(reply or '').strip() or not str(reason or '').strip():
            raise ValueError('reply and reason must not be empty')
        targets = targets if targets is not None else (context or {}).get('external_reply_targets')
        if not targets:
            raise ValueError('no external reply targets are available')
        return self.suggestion_result(
            content=str(reply).strip(),
            targets=targets,
            suggestion_text='Send a reply to the linked external record',
            tool_name='suggest_reply',
        )
