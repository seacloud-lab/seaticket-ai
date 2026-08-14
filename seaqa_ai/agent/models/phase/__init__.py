from copy import deepcopy
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
    default_final_output_schema: ClassVar[dict] = {}

    @property
    def final_output_schema(self) -> dict:
        schema = deepcopy(self.default_final_output_schema)
        additional_output = self.skill_metadata.get('additional_output', {})
        properties = schema.setdefault('properties', {})
        conflicting_fields = sorted(set(properties) & set(additional_output))
        if conflicting_fields:
            raise ValueError(
                f'Skill {self.event}/{self.phase} additional_output conflicts with '
                f'default output fields: {", ".join(conflicting_fields)}'
            )
        additional_properties = {
            name: {
                key: value
                for key, value in property_schema.items()
                if key != 'required'
            }
            for name, property_schema in additional_output.items()
        }
        properties.update(additional_properties)
        required = schema.setdefault('required', [])
        required.extend(
            name
            for name, property_schema in additional_output.items()
            if property_schema.get('required', True)
        )

        return schema if properties else {}

    @property
    def additional_output_fields(self) -> set[str]:
        return set(self.skill_metadata.get('additional_output', {}))

    @cached_property
    def skill(self) -> str:
        from seaqa_ai.agent.utils import get_skill_map

        try:
            return get_skill_map()[self.event][self.phase]
        except KeyError as error:
            raise ValueError(
                f'No {self.phase!r} skill configured for event {self.event!r}'
            ) from error

    @cached_property
    def skill_metadata(self) -> dict:
        metadata = yaml.safe_load(self.skill.split('====================', 1)[0])
        if not isinstance(metadata, dict):
            raise ValueError(f'Skill {self.event}/{self.phase} metadata must be an object')
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
