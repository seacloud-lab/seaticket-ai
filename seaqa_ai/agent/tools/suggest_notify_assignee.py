from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils.record_identity import record_identity_schema


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
                    **record_identity_schema(include_source_title=True),
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
