from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils.record_identity import record_identity_schema


class SuggestNotifyAssignee(BasicAgentSuggestionTool):
    suggestion_title = 'Notify assignees of the ticket'
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
        if target is None and (context or {}).get('source_type') != 'ticket':
            raise ValueError('notification target is required outside a ticket task')
        target = self._loaded_target(target, context, 'ticket')
        return self.suggestion_result(
            suggestion_content=message,
            targets=[target],
            suggestion_reason=reason,
        )
