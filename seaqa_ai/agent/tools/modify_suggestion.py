"""Edit one existing regeneration draft without creating a business action."""

from seaqa_ai.utils.tools import BasicTool


class ModifySuggestion(BasicTool):
    tool = {
        'type': 'function',
        'name': 'modify_suggestion',
        'description': (
            'Modify an existing draft by suggestion_id. Omitted fields are preserved. '
            'Use suggestion_content for reply or notification text. Use ticket_fields for ticket suggestions. '
            'Use suggest_* tools only to add new suggestions.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'suggestion_id': {'type': 'string'},
                'changes': {
                    'type': 'object',
                    'properties': {
                        'suggestion_content': {
                            'type': 'string',
                            'description': 'Complete replacement text for a reply or notification. Do not use this for ticket suggestions.',
                        },
                        'suggestion_reason': {'type': 'string'},
                        'ticket_fields': {
                            'type': 'object',
                            'description': 'Fields to merge into an existing ticket suggestion. Unspecified ticket fields stay unchanged.',
                            'properties': {
                                'title': {'type': 'string'},
                                'content': {'type': 'string'},
                                'type': {'type': 'string'},
                                'priority': {'type': 'integer', 'minimum': 0},
                                'tags': {'type': 'array', 'items': {'type': 'string'}},
                            },
                            'minProperties': 1,
                            'additionalProperties': False,
                        },
                        'suggestion_payload': {
                            'type': 'object',
                            'properties': {
                                'suggested_labels': {'type': 'array', 'items': {'type': 'string'}},
                                'related_ticket': {'type': 'integer', 'minimum': 1},
                                'suggested_type': {'type': 'string', 'enum': ['Bug', 'Feature', 'Question']},
                            },
                            'additionalProperties': False,
                        },
                    },
                    'minProperties': 1,
                    'additionalProperties': False,
                },
            },
            'required': ['suggestion_id', 'changes'],
            'additionalProperties': False,
        },
        'strict': False,
    }

    def execute(self, suggestion_id, changes, context=None):
        context = context or {}
        store = context.get('suggestion_drafts')
        if store is None:
            raise ValueError('modify_suggestion is available only during regeneration')
        return store.modify(suggestion_id, changes, context)
