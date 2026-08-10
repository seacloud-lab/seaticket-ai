from pathlib import Path

from seaqa_ai.agent.models.phase import AgentPhaseSpec


class PreludePhaseSpec(AgentPhaseSpec):
    """Specification for the event-context preparation phase."""

    final_output_schema = {
        'type': 'object',
        'properties': {
            'event_summary': {
                'type': 'string',
                'description': 'A concise summary of the triggering event.',
            },
            'event_related_records': {
                'type': 'array',
                'description': (
                    'Successfully loaded record identities selected by Prelude for this event. '
                    'The triggering record is the first entry.'
                ),
                'items': {
                    'type': 'object',
                    'properties': {
                        'source_type': {'type': 'string'},
                        'connection_id': {'type': ['integer', 'null']},
                        'record_id': {'type': 'integer'},
                    },
                    'required': ['source_type', 'connection_id', 'record_id'],
                    'additionalProperties': False,
                },
            },
        },
        'required': [
            'event_summary',
            'event_related_records',
        ],
        'additionalProperties': True,
    }

    @classmethod
    def get_system_prompt(cls) -> str:
        return (Path(__file__).with_name('system_prompt.xml')).read_text(encoding='utf-8')
