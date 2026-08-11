from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils.record_identity import record_identity_schema


class SuggestReplyExternalTargets(BasicAgentSuggestionTool):
    suggestion_text = 'Send a reply'
    tool = {
        'type': 'function', 'name': 'suggest_reply_external_targets',
        'description': 'Create a pending public reply suggestion for linked external records.',
        'parameters': {
            'type': 'object',
            'properties': {
                'reply': {'type': 'string'},
                'reason': {'type': 'string'},
                'reply_targets': {
                    'type': 'array',
                    'items': record_identity_schema(include_source_title=True),
                    'minItems': 1,
                    'description': 'Linked external records that should receive the reply.',
                },
            },
            'required': ['reply', 'reason', 'reply_targets'], 'additionalProperties': False,
        }, 'strict': True,
    }

    def execute(self, reply, reason, reply_targets, context=None):
        reply = str(reply or '').strip()
        reason = str(reason or '').strip()
        if not reply or not reason:
            raise ValueError('reply and reason must not be empty')
        if not isinstance(reply_targets, list):
            raise ValueError('reply_targets must be an array')

        linked_source_ids = self._linked_source_ids(context)
        trigger_source_id = str((context or {}).get('trigger_source_id', '') or '').strip()
        targets = []
        seen_source_ids = set()
        for reply_target in reply_targets:
            normalized_target = self._normalize_target(reply_target)
            source_id = normalized_target['source_id']
            if (
                source_id not in linked_source_ids
                or source_id == trigger_source_id
                or source_id in seen_source_ids
            ):
                continue
            seen_source_ids.add(source_id)
            targets.append(normalized_target)
        if not targets:
            raise ValueError('no external reply targets are available')
        return self.suggestion_result(
            suggestion_content=reply,
            targets=targets,
            suggestion_reason=reason,
            suggestion_text='Send a reply to the linked external record',
            tool_name='suggest_reply',
        )
