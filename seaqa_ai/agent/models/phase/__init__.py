from dataclasses import dataclass
from typing import ClassVar

import yaml

@dataclass(frozen=True)
class AgentPhaseSpec:
    event: str
    phase: str
    message_input: str
    context: dict
    max_steps = 10
    max_steps_prompt = 'Maximum tool-call steps reached. Return the final answer without calling tools.'
    final_output_schema: ClassVar[dict]

    def get_skill(self) -> str:
        from seaqa_ai.agent.utils import get_skill_map

        try:
            return get_skill_map()[self.event][self.phase]
        except KeyError as error:
            raise ValueError(
                f'No {self.phase!r} skill configured for event {self.event!r}'
            ) from error
    
    def get_tools(self) -> list:
        from seaqa_ai.agent.utils import get_tools_map

        skill = self.get_skill()
        metadata = yaml.safe_load(skill.split('====================', 1)[0])
        tool_names = metadata.get('tools', [])
        if not isinstance(tool_names, list) or not all(isinstance(name, str) for name in tool_names):
            raise ValueError(f'Skill {self.event}/{self.phase} has invalid tools metadata')
        if self.phase != 'prelude' and 'get_record' in tool_names:
            raise ValueError(f'Only prelude skills may use get_record: {self.event}/{self.phase}')
        tools_map = get_tools_map()
        unknown_tools = sorted(set(tool_names) - set(tools_map))
        if unknown_tools:
            raise ValueError(
                f'Skill {self.event}/{self.phase} references unknown tools: {", ".join(unknown_tools)}'
            )
        return [
            tools_map[tool]
            for tool in tool_names
        ]
    
    @classmethod
    def get_system_prompt(cls) -> str:
        raise NotImplementedError()
