from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSummary:
    id: str
    name: str
    description: str
    required_tools: tuple[str, ...] = ()
    support_external_portal: bool = False


@dataclass(frozen=True)
class SkillSpec(SkillSummary):
    system_prompt: str = ''
