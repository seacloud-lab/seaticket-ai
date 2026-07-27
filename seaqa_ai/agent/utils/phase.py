from dataclasses import dataclass, field

from seaqa_ai.agent.prompts._forced import (
    FORCED_FINAL_ANSWER_PROMPT,
    FORCED_RECORD_ANALYSIS_PROMPT,
)
from seaqa_ai.agent.skills.spec import AgentSkillSpec
from seaqa_ai.utils.memory import OpenAIMemory


@dataclass
class Phase:
    """Base dataclass describing one reasoning phase.

    Attributes:
        name: Human-readable label ('prelude', 'analysis', 'handling').
        system_prompt: System message injected at the start of the phase.
        input: Runtime input rendered for the phase (set during run).
        memory: OpenAIMemory constructed from system_prompt + input.
    """

    name: str
    system_prompt: str = ''
    input: str = field(default='', init=False, repr=False)
    memory: OpenAIMemory | None = field(default=None, init=False, repr=False)

    def init_memory(self):
        self.memory = OpenAIMemory([
            {'role': 'system', 'content': self.system_prompt},
            {'role': 'user', 'content': self.input},
        ])


@dataclass
class AskPhase(Phase):
    """Single-turn Q&A phase (no tools, no loop).

    Attributes:
        schema: Optional JSON schema dict to validate the LLM response.
        output: Runtime output parsed from the LLM response (set during run).
    """

    schema: dict | None = None
    output: dict | None = field(default=None, init=False, repr=False)


@dataclass
class ReactPhase(Phase):
    """Multi-step ReAct tool-calling phase.

    Attributes:
        terminator_tool: Name of the tool whose invocation ends the phase.
        terminator_prompt: System message injected when max_steps is reached
            to push the LLM to call terminator_tool.
        max_steps: Maximum number of LLM steps before forced termination.
        tools: OpenAI function-calling tool dicts available during this phase.
    """

    terminator_tool: str = ''
    terminator_prompt: str = ''
    max_steps: int = 10
    tools: list[dict] = field(default_factory=list)


def build_analysis_phase(skill: AgentSkillSpec, prompt_builder) -> ReactPhase:
    return ReactPhase(
        name='analysis',
        system_prompt=prompt_builder(skill.analysis.prompt),
        terminator_tool=skill.analysis.terminator,
        terminator_prompt=FORCED_RECORD_ANALYSIS_PROMPT,
        max_steps=skill.analysis.max_steps,
    )


def build_handling_phase(skill: AgentSkillSpec, prompt_builder) -> ReactPhase:
    return ReactPhase(
        name='handling',
        system_prompt=prompt_builder(skill.handling.prompt),
        terminator_tool=skill.handling.terminator,
        terminator_prompt=FORCED_FINAL_ANSWER_PROMPT,
        max_steps=skill.handling.max_steps,
    )


def resolve_phase_tools(available_tools: list[dict], tool_names: tuple[str, ...]) -> list[dict]:
    tools_by_name = {}
    for tool in available_tools:
        name = str(tool.get('name') or '').strip()
        if name and name not in tools_by_name:
            tools_by_name[name] = tool

    resolved_tools = []
    missing_tools = []
    for tool_name in tool_names:
        tool = tools_by_name.get(tool_name)
        if not tool:
            missing_tools.append(tool_name)
            continue
        resolved_tools.append(tool)

    if missing_tools:
        missing = ', '.join(missing_tools)
        raise ValueError(f'Missing required tools: {missing}')
    return resolved_tools
