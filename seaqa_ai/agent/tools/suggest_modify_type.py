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

    def execute(self, current_issue_type, suggested_type, reason):
        if not str(reason or '').strip():
            raise ValueError('reason must not be empty')
        return {'status': 'pending', 'suggestion_content': str(suggested_type)}

    def get_suggestion_text(self, *, tool_arguments, result, context):
        current = str(tool_arguments.get('current_issue_type') or '').strip()
        suggested = tool_arguments['suggested_type']
        return (
            f'Suggest changing the type from "{current}" to "{suggested}" for this GitHub issue'
            if current else f'Suggest changing the type to "{suggested}" for this GitHub issue'
        )
