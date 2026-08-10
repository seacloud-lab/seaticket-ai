from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestCloseTicket(BasicAgentSuggestionTool):
    suggestion_text = 'Close this ticket'
    tool = {
        'type': 'function', 'name': 'suggest_close_ticket',
        'description': 'Create a pending suggestion to close the current or specified ticket.',
        'parameters': {
            'type': 'object',
            'properties': {
                'reason': {'type': 'string'},
                'rule': {'type': 'string'},
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
            'required': ['reason'], 'additionalProperties': False,
        }, 'strict': True,
    }

    def execute(self, reason, rule='', target=None, context=None):
        reason = str(reason or '').strip()
        if not reason:
            raise ValueError('reason must not be empty')
        if target is not None and (
            not isinstance(target, dict)
            or str(target.get('source_type', '') or '').strip() != 'ticket'
        ):
            raise ValueError('suggest_close_ticket target must be a ticket')
        if target is None and (context or {}).get('source_type') != 'ticket':
            raise ValueError('suggest_close_ticket target is required outside a ticket task')
        return self.suggestion_result(
            suggestion_content=reason, context=context, targets=[target] if target else None,
            suggestion_reason=reason,
        )
