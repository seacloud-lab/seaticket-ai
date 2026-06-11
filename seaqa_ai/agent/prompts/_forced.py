# System-injected prompts used by AgentRunner for forced tool-call steps.
# These are appended as a trailing system message to steer the LLM when the
# normal reasoning loop needs to be terminated or seeded.

FORCED_RECORD_ANALYSIS_PROMPT = (
    'Analysis loop reached max steps without calling the required analysis tool.\n'
    'Now call it immediately using only the best available evidence from the input and prior search results.\n'
    'Fill all required fields.\n'
    'If evidence is insufficient, explicitly state what is uncertain instead of implying confidence.\n'
    'Do not introduce claims that were not supported by the input or prior search results.\n'
    'Use conservative defaults when needed.'
)

FORCED_FINAL_ANSWER_PROMPT = (
    'Handling loop reached max steps without calling final_answer.\n'
    'Now call final_answer immediately.\n'
    'Summarize only what was actually done and any remaining uncertainty.\n'
    'Do not introduce new claims that were not supported by the input or prior tool results.'
)
