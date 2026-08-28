from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool


class SuggestLinkExistingTicket(BasicAgentSuggestionTool):
    tool = {
        'type': 'function',
        'name': 'suggest_link_existing_ticket',
        'description': (
            'Create a pending suggestion to link this record to an existing internal ticket '
            'when search evidence shows similar records are already tracked there.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'related_ticket': {
                    'type': 'integer',
                    'description': 'The internal ticket number this record should link to.',
                },
                'reason': {'type': 'string'},
            },
            'required': ['related_ticket', 'reason'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, related_ticket, reason, context=None):
        reason = str(reason or '').strip()
        if not reason:
            raise ValueError('reason must not be empty')
        try:
            related_ticket = int(related_ticket)
        except (TypeError, ValueError) as error:
            raise ValueError('related_ticket must be a positive integer') from error
        if related_ticket <= 0:
            raise ValueError('related_ticket must be a positive integer')
        self._loaded_target(
            {
                'source_type': 'ticket',
                'source_id': related_ticket,
            },
            context,
            required_source_type='ticket',
        )
        return self.suggestion_result(
            context=context,
            targets=[self._target_from_trigger_context(context)],
            suggestion_reason=reason,
            suggestion_payload={'related_ticket': related_ticket},
        )
