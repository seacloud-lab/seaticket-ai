from dataclasses import dataclass


@dataclass(frozen=True)
class SkillSpec:
    id: str
    name: str
    description: str
    system_prompt: str
    required_tools: tuple[str, ...] = ()
    supports_external_portal: bool = True
