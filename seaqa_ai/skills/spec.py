from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSummary:
    name: str
    description: str
    support_agent: bool = False


@dataclass(frozen=True)
class SkillSpec(SkillSummary):
    content: str = ''
