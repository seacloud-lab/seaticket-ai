from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestNotifyAssignee(BasicAgentSuggestionTool):
    suggestion_text = 'Notify assignees of the ticket'
    tool = {
        'type': 'function', 'name': 'suggest_notify_assignee',
        'description': 'Create a pending suggestion to notify ticket assignees.',
        'parameters': {
            'type': 'object',
            'properties': {'message': {'type': 'string'}, 'reason': {'type': 'string'}},
            'required': ['message', 'reason'], 'additionalProperties': False,
        }, 'strict': True,
    }

    def execute(self, message, reason, context=None):
        message = str(message or '').strip()
        if not message or not str(reason or '').strip():
            raise ValueError('message and reason must not be empty')
        return self.suggestion_result(content=message, context=context)
