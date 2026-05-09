"""
Agent tools specific to Discourse topic processing.

The agent uses this flow for each topic:
  1. record_issue_analysis  — called after search, before handling.
  2a. suggest_reply         — if the topic can be answered by known info.
  2b. suggest_create_ticket — if the topic should become a tracked ticket.
"""
from seaqa_ai.agent.tools.common_tools import (
    RecordIssueAnalysisTool,
    SuggestCreateTicketTool,
    SuggestReplyTool,
)



DISCOURSE_TOOL_CLASSES = [
    RecordIssueAnalysisTool,
    SuggestReplyTool,
    SuggestCreateTicketTool,
]
