from dataclasses import dataclass, field


@dataclass
class PhaseSpec:
    """Base spec describing one reasoning phase.

    Attributes:
        name: Human-readable label ('prelude', 'analysis', 'handling').
        system_prompt: System message injected at the start of the phase.
        input: Runtime input rendered for the phase (set during run).
    """

    name: str
    system_prompt: str = ''
    input: str = field(default='', init=False, repr=False)


@dataclass
class AskPhaseSpec(PhaseSpec):
    """Single-turn Q&A phase (no tools, no loop).

    Attributes:
        schema: Optional JSON schema dict to validate the LLM response.
        output: Runtime output parsed from the LLM response (set during run).
    """

    schema: dict | None = None
    output: dict | None = field(default=None, init=False, repr=False)


@dataclass
class ReactPhaseSpec(PhaseSpec):
    """Multi-step ReAct tool-calling phase.

    Attributes:
        terminator_tool: Name of the tool whose invocation ends the phase.
        forced_prompt: System message injected when max_steps is reached
            to force an immediate terminator call.
        max_steps: Maximum number of LLM steps before forced termination.
    """

    terminator_tool: str = ''
    forced_prompt: str = ''
    max_steps: int = 10
