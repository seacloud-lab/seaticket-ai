from pathlib import Path

from seaqa_ai.agent.models.phase import AgentPhaseSpec
from seaqa_ai.agent.utils.record_identity import record_identity_schema


class PreludePhaseSpec(AgentPhaseSpec):
    """Specification for the event-context preparation phase."""

    base_final_output_schema = {
        'type': 'object',
        'properties': {
            'event_summary': {
                'type': 'string',
                'description': (
                    'A concise summary of the triggering event. Do not include the event '
                    'trigger time or timestamp; the frontend presents it separately.'
                ),
            },
            'event_related_records': {
                'type': 'array',
                'description': (
                    'Successfully loaded record identities selected by Prelude for this event. '
                    'The triggering record is the first entry.'
                ),
                'items': record_identity_schema(),
            },
        },
        'required': [
            'event_summary',
            'event_related_records',
        ],
        'additionalProperties': False,
    }

    @classmethod
    def get_system_prompt(cls) -> str:
        return (Path(__file__).with_name('system_prompt.xml')).read_text(encoding='utf-8')
