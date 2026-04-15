"""
Agent tools specific to email thread processing.

The agent uses this flow for each email thread:
  1. record_issue_analysis  — called after search, before handling.
  2a. suggest_resolution    — if the thread can be answered by known info.
  2b. suggest_create_ticket — if the thread should become a tracked ticket.
"""
from seaqa_ai.agent.tools.common_tools import (
    RecordIssueAnalysisTool,
    SuggestCreateTicketTool,
    SuggestResolutionTool,
)


EMAIL_TOOL_CLASSES = [
    RecordIssueAnalysisTool,
    SuggestResolutionTool,
    SuggestCreateTicketTool,
]
