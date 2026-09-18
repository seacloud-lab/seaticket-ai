from pathlib import Path

from seaqa_ai.agent.models.phase import AgentPhaseSpec


class RegenerationPhaseSpec(AgentPhaseSpec):
    """Specification for user-instruction-driven suggestion regeneration."""

    @classmethod
    def get_system_prompt(cls) -> str:
        return (Path(__file__).with_name('system_prompt.xml')).read_text(encoding='utf-8')
