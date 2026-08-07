from pathlib import Path

from seaqa_ai.agent.models.phase import AgentPhaseSpec


class HandlingPhaseSpec(AgentPhaseSpec):
    """Specification for the action recommendation and execution phase."""

    final_output_schema = {
        'type': 'object',
        'properties': {
            'handling_report': {
                'type': 'string',
                'description': (
                    'A concise, auditable report of the handling decision and completed '
                    'or proposed actions, including their evidence, outcomes, and any '
                    'intentional omissions, constraints, or failures.'
                ),
            },
        },
        'required': ['handling_report'],
        'additionalProperties': True,
    }

    @classmethod
    def get_system_prompt(cls) -> str:
        return (Path(__file__).with_name('system_prompt.xml')).read_text(encoding='utf-8')
