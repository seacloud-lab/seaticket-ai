"""
Agent tools specific to email thread processing.

The agent uses this flow for each email thread:
  1. record_issue_analysis     — record analysis before handling.
  2a. suggest_reply            — if the thread can be answered by known info.
  2b. suggest_create_ticket    — if the thread should become a tracked ticket.
"""
from seaqa_ai.agent.tools.common_tools import (
    RecordIssueAnalysisTool,
    SuggestCreateTicketTool,
    SuggestReplyTool,
)


EMAIL_TOOL_CLASSES = [
    RecordIssueAnalysisTool,
    SuggestReplyTool,
    SuggestCreateTicketTool,
]
