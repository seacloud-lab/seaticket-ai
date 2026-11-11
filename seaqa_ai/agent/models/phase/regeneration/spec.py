from functools import cached_property
from pathlib import Path

from seaqa_ai.agent.models.phase import AgentPhaseSpec


class RegenerationPhaseSpec(AgentPhaseSpec):
    """Specification for user-instruction-driven suggestion regeneration."""

    @cached_property
    def tools(self) -> list:
        from seaqa_ai.agent.tools.modify_suggestion import ModifySuggestion
        from seaqa_ai.agent.tools.remove_suggestion import RemoveSuggestion

        return [ModifySuggestion, RemoveSuggestion, *super().tools]

    @classmethod
    def get_system_prompt(cls) -> str:
        return (Path(__file__).with_name('system_prompt.xml')).read_text(encoding='utf-8')
