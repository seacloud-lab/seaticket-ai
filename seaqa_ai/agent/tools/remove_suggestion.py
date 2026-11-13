"""Remove one existing regeneration draft without executing a business action."""

from seaqa_ai.utils.tools import BasicTool


class RemoveSuggestion(BasicTool):
    tool = {
        'type': 'function',
        'name': 'remove_suggestion',
        'description': (
            'Remove an existing draft by suggestion_id. '
            'Call this before adding a different mutually exclusive suggestion for the same target.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'suggestion_id': {'type': 'string'},
                'reason': {'type': 'string'},
            },
            'required': ['suggestion_id', 'reason'],
            'additionalProperties': False,
        },
        'strict': False,
    }

    def execute(self, suggestion_id, reason, context=None):
        context = context or {}
        store = context.get('suggestion_drafts')
        if store is None:
            raise ValueError('remove_suggestion is available only during regeneration')
        return store.remove(suggestion_id, reason)
