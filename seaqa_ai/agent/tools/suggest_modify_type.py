from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestModifyType(BasicAgentSuggestionTool):
    tool = {
        'type': 'function', 'name': 'suggest_modify_type',
        'description': 'Create a pending suggestion to change a GitHub issue type.',
        'parameters': {
            'type': 'object',
            'properties': {
                'current_issue_type': {'type': 'string'},
                'suggested_type': {'type': 'string', 'enum': ['Bug', 'Feature', 'Question']},
                'reason': {'type': 'string'},
            },
            'required': ['current_issue_type', 'suggested_type', 'reason'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, current_issue_type, suggested_type, reason, context=None):
        if not str(reason or '').strip():
            raise ValueError('reason must not be empty')
        current = str(current_issue_type or '').strip()
        suggestion_text = (
            f'Suggest changing the type from "{current}" to "{suggested_type}" for this GitHub issue'
            if current else f'Suggest changing the type to "{suggested_type}" for this GitHub issue'
        )
        return self.suggestion_result(
            context=context,
            targets=[self._target_from_trigger_context(context)],
            suggestion_reason=reason,
            suggestion_text=suggestion_text,
        )
