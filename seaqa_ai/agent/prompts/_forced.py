# System-injected prompts used by AgentRunner for forced tool-call steps.
# These are appended as a trailing system message to steer the LLM when the
# normal reasoning loop needs to be terminated or seeded.

_THINK_LENGTH_CONSTRAINT = (
    '\n\nIMPORTANT: Keep the thought concise — use 3-8 short bullet points (under 1000 words total). '
    'Do NOT copy-paste raw content, search results, or comment text. Summarise key points only.'
)

FORCED_RECORD_EVENT_SUMMARY_PROMPT = (
    'Before doing any analysis, call record_event_summary immediately.\n'
    'Summarise only the input event: what type of event occurred, what changed, and what the main information is.\n'
    'Use the event type plus any event payload section (for example comment/reply) when available.\n'
    'Focus on event-related sections; be concise and avoid repeating the full record content.'
)

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

PHASE_GUIDANCE_FOR_THINK = {
    'ticket_analysis': (
        'You are in the ANALYSIS phase. Before the next action, call the think tool to:\n'
        '- Review the ticket details: state, priority, assignees, due date, and recent activities.\n'
        '- Identify any process issues (e.g., approaching deadline, missing assignee, etc.).\n'
        '- Decide what actions are needed, then call record_ticket_analysis to finalise.'
        + _THINK_LENGTH_CONSTRAINT
    ),
    'issue_analysis': (
        'You are in the ANALYSIS phase. Before the next action, call the think tool to:\n'
        '- Summarise what you learned from the latest search results.\n'
        '- Identify remaining knowledge gaps that require further search.\n'
        '- Decide whether to search again or call record_issue_analysis to finalise.'
        + _THINK_LENGTH_CONSTRAINT
    ),
    'handling': (
        'You are in the HANDLING phase. Before the next action, call the think tool to:\n'
        '- Review the recorded analysis and available evidence.\n'
        '- Determine which handling action (suggest_reply, suggest_create_ticket, etc.) fits best.'
        + _THINK_LENGTH_CONSTRAINT
    ),
}
