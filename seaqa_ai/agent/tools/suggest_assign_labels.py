from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils.suggestion_validation import validate_labels


class SuggestAssignLabels(BasicAgentSuggestionTool):
    editable_payload_fields = frozenset({'suggested_labels'})
    tool = {
        'type': 'function',
        'name': 'suggest_assign_labels',
        'description': 'Create a pending suggestion to assign existing GitHub issue labels.',
        'parameters': {
            'type': 'object',
            'properties': {
                'suggested_labels': {'type': 'array', 'items': {'type': 'string'}},
                'reason': {'type': 'string'},
            },
            'required': ['suggested_labels', 'reason'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, suggested_labels, reason, context=None):
        if not str(reason or '').strip():
            raise ValueError('suggested_labels and reason must not be empty')
        available = (context or {}).get('trigger_record', {}).get('available_labels')
        labels = validate_labels(suggested_labels, available)
        return self.suggestion_result(
            context=context,
            targets=[self._target_from_trigger_context(context)],
            suggestion_reason=reason,
            suggestion_payload={'suggested_labels': labels},
        )
