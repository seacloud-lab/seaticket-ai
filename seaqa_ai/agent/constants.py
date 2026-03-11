
# How often (seconds) the scheduler syncs project configs from the DB.
# Determines how quickly changes to agent.enabled / run_interval_hours take effect.
CONFIG_SYNC_INTERVAL_SECONDS = 60


GITHUB_ISSUE_CONNECTION_TYPE = 'github_issue'

AGENT_RUNS_TABLE = 'agent_runs'
AGENT_ACTIONS_TABLE = 'agent_actions'

TICKETS_TABLE = 'tickets'
TICKET_COMMENTS_TABLE = 'ticket_comments'
TICKET_ACTIVITIES_TABLE = 'ticket_activities'

# Maximum body characters sent to the LLM to avoid excessive token usage
MAX_CONTENT_CHARS = 5000
MAX_COMMENT_CHARS = 1000

# Connection types considered as ISSUE category for related records search
ISSUE_CONNECTION_TYPES = ('github_issue', 'discourse_forum', 'email')

# Minimum similarity score to consider a related record as a known issue
RELATED_RECORD_SCORE_THRESHOLD = 0.5
