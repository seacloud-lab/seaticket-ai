from enum import Enum

AGENT_TASK_QUEUE_NAME = 'agent_task'

DATA_EVENTS_CHANNEL = 'data_events'

class DataEventType(Enum):
    TICKET_ADDED = 'ticket_added'
    TICKET_UPDATED = 'ticket_updated'
    TICKET_CLOSED = 'ticket_closed'
    TICKET_REOPENED = 'ticket_reopened'
    TICKET_COMMENT_ADDED = 'ticket_comment_added'
    TICKET_COMMENT_UPDATED = 'ticket_comment_updated'
    TICKET_RESTORED = 'ticket_restored'
    GITHUB_ISSUE_ADDED  = 'github_issue_added'
    GITHUB_ISSUE_UPDATED = 'github_issue_updated'
    GITHUB_ISSUE_CLOSED = 'github_issue_closed'
    GITHUB_ISSUE_REOPENED = 'github_issue_reopened'
    GITHUB_ISSUE_COMMENT_ADDED = 'github_issue_comment_added'

    @classmethod
    def is_valid(cls, value):
        return value in {item.value for item in cls}

class SourceType(Enum):
    TICKET = 'ticket'
    GITHUB_ISSUE = 'github_issue'

    @classmethod
    def is_valid(cls, value):
        return value in {item.value for item in cls}

AGENT_RUNS_TABLE = 'agent_runs'
AGENT_ACTIONS_TABLE = 'agent_actions'

# Maximum body characters sent to the LLM to avoid excessive token usage
MAX_CONTENT_CHARS = 5000
MAX_COMMENT_CHARS = 1000

# Minimum similarity score to consider a related record as a known issue
RELATED_RECORD_SCORE_THRESHOLD = 0.5
