from enum import Enum

MODEL_USAGE_STATISTIC_CHANNEL_NAME = 'log_ai_model_usage'

class SeasearchType(Enum):
    SITE = 'site'
    SEAFILE = 'seafile'


class ConnectionType(Enum):
    EMAIL = 'email'
    GITHUB_ISSUE = 'github_issue'
    DISCOURSE_FORUM = 'discourse_forum'
    SITE = 'site'
    SEAFILE = 'seafile'
