from pathlib import Path
from typing import ClassVar

from seaqa_ai.agent.models.phase import AgentPhaseSpec


class HandlingPhaseSpec(AgentPhaseSpec):
    """Specification for the action recommendation and execution phase."""
    enable_extra_skills: ClassVar[bool] = True

    @classmethod
    def get_system_prompt(cls) -> str:
        return (Path(__file__).with_name('system_prompt.xml')).read_text(encoding='utf-8')
