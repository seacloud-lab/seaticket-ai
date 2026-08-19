from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar

import yaml

@dataclass(frozen=True)
class AgentPhaseSpec:
    event: str
    phase: str
    message_input: str
    records_input: dict | list
    context: dict
    max_steps = 10
    max_steps_prompt = 'Maximum tool-call steps reached. Return the final answer without calling tools.'
    base_final_output_schema: ClassVar[dict] = {}

    @property
    def final_output_schema(self) -> dict:
        from seaqa_ai.agent.utils.final_output_schema_utils import FinalOutputSchemaUtils

        return FinalOutputSchemaUtils.build(
            self.base_final_output_schema,
            self.skill_metadata.get('additional_output', {}),
            self.event,
            self.phase,
        )

    @cached_property
    def skill(self) -> str:
        return self.get_skill(self.event, self.phase)

    @classmethod
    def get_skill(cls, event: str, phase: str) -> str:
        from seaqa_ai.agent.utils import get_skill_map

        try:
            return get_skill_map()[event][phase]
        except KeyError as error:
            raise ValueError(
                f'No {phase!r} skill configured for event {event!r}'
            ) from error

    @cached_property
    def skill_metadata(self) -> dict:
        return self.get_skill_metadata(self.event, self.phase)

    @classmethod
    def get_skill_metadata(cls, event: str, phase: str) -> dict:
        metadata = yaml.safe_load(cls.get_skill(event, phase).split('====================', 1)[0])
        if not isinstance(metadata, dict):
            raise ValueError(f'Skill {event}/{phase} metadata must be an object')
        return metadata

    @cached_property
    def tools(self) -> list:
        from seaqa_ai.agent.utils import get_tools_map

        metadata = self.skill_metadata
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
