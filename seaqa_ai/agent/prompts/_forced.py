# System-injected prompts used by AgentRunner for forced tool-call steps.
# These are appended as a trailing system message to steer the LLM when the
# normal reasoning loop needs to be terminated or seeded.

FORCED_RECORD_ANALYSIS_PROMPT = (
    'Analysis loop reached max steps without calling the required analysis tool.\n'
    'Now call it immediately with the best available evidence.\n'
    'Fill all required fields. If some detail is uncertain, be explicit and use conservative defaults.'
)

FORCED_FINAL_ANSWER_PROMPT = (
    'Handling loop reached max steps without calling final_answer.\n'
    'Now call final_answer immediately.\n'
    'Provide a concise summary of what was done and any remaining uncertainty.'
)
