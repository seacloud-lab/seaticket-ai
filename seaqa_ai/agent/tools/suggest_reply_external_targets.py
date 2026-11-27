from copy import deepcopy

from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.utils.record.identity import record_identity_schema


def _reply_target_schema():
    schema = deepcopy(record_identity_schema(nested=True))
    for branch in schema.get('anyOf', []):
        props = branch.get('properties', {})
        source_enum = props.get('source_type', {}).get('enum', [])
        if 'email' in source_enum:
            props['reply_to'] = {
                'type': 'array',
                'items': {'type': 'string'},
                'description': (
                    'Optional email reply recipients when this email target is a forwarded '
                    'request that should be answered to the original sender.'
                ),
            }
    return schema


class SuggestReplyExternalTargets(BasicAgentSuggestionTool):
    persisted_tool_name = 'suggest_reply'
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
                    'items': _reply_target_schema(),
                    'minItems': 1,
                    'description': 'Linked external records that should receive the reply.',
                },
            },
            'required': ['reply', 'reason', 'reply_targets'],
            'additionalProperties': False,
        },
        'strict': False,
    }

    def execute(self, reply, reason, reply_targets, context=None):
        reply = str(reply or '').strip()
        reason = str(reason or '').strip()
        if not reply or not reason:
            raise ValueError('reply and reason must not be empty')
        if not isinstance(reply_targets, list):
            raise ValueError('reply_targets must be an array')

        reply_to_by_source_id = {}
        for raw_target in reply_targets:
            if not isinstance(raw_target, dict) or not raw_target.get('reply_to'):
                continue
            normalized = self._normalize_target(raw_target)
            if normalized['source_type'] == 'email':
                reply_to_by_source_id[normalized['source_id']] = raw_target.get('reply_to')

        targets = self._linked_external_targets(reply_targets, context)
        if not targets:
            raise ValueError('no external reply targets are available')

        for target in targets:
            raw_reply_to = reply_to_by_source_id.get(target['source_id'])
            normalized_reply_to = self._normalize_email_reply_to(raw_reply_to, target, context)
            if normalized_reply_to:
                target['suggestion_payload'] = {'reply_to': normalized_reply_to}

        return self.suggestion_result(
            suggestion_content=reply,
            targets=targets,
            suggestion_reason=reason,
            tool_name='suggest_reply',
        )
