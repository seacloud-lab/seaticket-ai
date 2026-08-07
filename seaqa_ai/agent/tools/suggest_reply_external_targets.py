from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestReplyExternalTargets(BasicAgentSuggestionTool):
    suggestion_text = 'Send a reply'
    tool = {
        'type': 'function', 'name': 'suggest_reply_external_targets',
        'description': 'Create pending public reply suggestions for system-provided external targets.',
        'parameters': {
            'type': 'object',
            'properties': {'reply': {'type': 'string'}, 'reason': {'type': 'string'}},
            'required': ['reply', 'reason'], 'additionalProperties': False,
        }, 'strict': True,
    }

    def execute(self, reply, reason, context=None):
        if not str(reply or '').strip() or not str(reason or '').strip():
            raise ValueError('reply and reason must not be empty')
        if not (context or {}).get('external_reply_targets'):
            raise ValueError('no external reply targets are available')
        return {'status': 'pending', 'suggestion_content': str(reply).strip()}

    def get_suggestion_targets(self, *, tool_arguments, result, context):
        targets = []
        for target in (context or {}).get('external_reply_targets', []):
            if not isinstance(target, dict):
                continue
            targets.append({
                **target,
                'tool_name': 'suggest_reply',
                'suggestion_text': 'Send a reply to the linked external record',
            })
        return targets
