from dataclasses import dataclass, field

from seaqa_ai.services.chat_service.utils.memory import OpenAIMemory


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
