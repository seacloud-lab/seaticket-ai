from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestNotifyAssignee(BasicAgentSuggestionTool):
    suggestion_text = 'Notify assignees of the ticket'
    tool = {
        'type': 'function', 'name': 'suggest_notify_assignee',
        'description': 'Create a pending suggestion to notify ticket assignees.',
        'parameters': {
            'type': 'object',
            'properties': {
                'message': {'type': 'string'},
                'reason': {'type': 'string'},
                'target': {
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
            'required': ['message', 'reason'], 'additionalProperties': False,
        }, 'strict': True,
    }

    def execute(self, message, reason, target=None, context=None):
        message = str(message or '').strip()
        if not message or not str(reason or '').strip():
            raise ValueError('message and reason must not be empty')
        if target is not None and target.get('source_type') != 'ticket':
            raise ValueError('notification target must be a ticket')
        if target is None and (context or {}).get('source_type') != 'ticket':
            raise ValueError('notification target is required outside a ticket task')
        return self.suggestion_result(
            suggestion_content=message,
            context=context,
            targets=[target] if target else None,
            suggestion_reason=reason,
        )
