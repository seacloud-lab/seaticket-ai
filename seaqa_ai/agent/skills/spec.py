from dataclasses import dataclass


@dataclass(frozen=True)
class SkillPhaseSpec:
    prompt: str
    tools: tuple[str, ...]
    terminator: str
    max_steps: int


@dataclass(frozen=True)
class AgentSkillSpec:
    id: str
    description: str
    source_types: tuple[str, ...]
    event_types: tuple[str, ...]
    analysis: SkillPhaseSpec
    handling: SkillPhaseSpec
