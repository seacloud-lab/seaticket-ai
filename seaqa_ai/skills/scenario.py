from dataclasses import dataclass
from enum import Enum

AGENT_SCENARIO_CONTEXT_KEY = 'is_agent_scenario'


class SkillScenarioKind(Enum):
    CHAT = 'chat'
    AGENT = 'agent'


@dataclass(frozen=True)
class SkillScenario:
    kind: SkillScenarioKind

    @classmethod
    def from_context(cls, context):
        context = context or {}
        kind = SkillScenarioKind.AGENT if context.get(AGENT_SCENARIO_CONTEXT_KEY) else SkillScenarioKind.CHAT
        return cls(kind=kind)
