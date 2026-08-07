import json

from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestAssignLabels(BasicAgentSuggestionTool):
    suggestion_text = 'Suggest assigning labels to this GitHub issue'
    tool = {
        'type': 'function', 'name': 'suggest_assign_labels',
        'description': 'Create a pending suggestion to assign existing GitHub issue labels.',
        'parameters': {
            'type': 'object',
            'properties': {
                'suggested_labels': {'type': 'array', 'items': {'type': 'string'}},
                'reason': {'type': 'string'},
            },
            'required': ['suggested_labels', 'reason'], 'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, suggested_labels, reason, context=None):
        labels = [str(label).strip() for label in suggested_labels if str(label).strip()]
        if not labels or not str(reason or '').strip():
            raise ValueError('suggested_labels and reason must not be empty')
        available = (context or {}).get('target_record', {}).get('available_labels') or []
        available_normalized = {str(label).casefold() for label in available}
        if available_normalized and any(label.casefold() not in available_normalized for label in labels):
            raise ValueError('suggested_labels must be selected from available_labels')
        return {'status': 'pending', 'suggestion_content': json.dumps(labels, ensure_ascii=False)}
