from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestLinkExistingTicket(BasicAgentSuggestionTool):
    suggestion_text = 'Link this record to an existing internal ticket'
    tool = {
        'type': 'function', 'name': 'suggest_link_existing_ticket',
        'description': (
            'Create a pending suggestion to link this record to an existing internal '
            'ticket when search evidence shows similar records are already tracked there.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'related_ticket': {
                    'type': 'integer',
                    'description': 'The id of the existing internal ticket that should be linked to this record.',
                },
                'reason': {
                    'type': 'string',
                    'description': 'Explain why this record should be linked to the existing ticket instead of creating a new one.',
                },
            },
            'required': ['related_ticket', 'reason'], 'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, related_ticket, reason, context=None):
        if isinstance(related_ticket, bool) or not isinstance(related_ticket, int) or related_ticket <= 0:
            raise ValueError('related_ticket must be a positive integer')
        reason = str(reason or '').strip()
        if not reason:
            raise ValueError('reason must not be empty')
        return self.suggestion_result(
            context=context,
            targets=[self._target_from_trigger_context(context)],
            suggestion_reason=reason,
            suggestion_text=f'Link this record to the existing ticket #{related_ticket}',
            suggestion_payload={'related_ticket': related_ticket},
        )
