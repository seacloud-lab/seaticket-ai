from enum import Enum

MODEL_USAGE_STATISTIC_CHANNEL_NAME = 'log_ai_model_usage'
EXTRA_CONTENTS_PROMPT = 'Here are some tickets or issues in Json format with title, content and comments. \n\n'

class SeasearchType(Enum):
    SITE = 'site'
    SEAFILE = 'seafile'

class ASK_MODE_PROPERTIES_MAP(Enum):
    RESOLVE_TYPE = 'ask'
    EXECUTE_AGENT = 'function_calling'
    MEMORY = 'openai'

class AGENT_MODE_PROPERTIES_MAP(Enum):
    RESOLVE_TYPE = 'agent'
    EXECUTE_AGENT = 'reasoning_agent'
    MEMORY = 'smolagents'

class ConnectionType(Enum):
    EMAIL = 'email'
    GITHUB_ISSUE = 'github_issue'
    DISCOURSE_FORUM = 'discourse_forum'
    SITE = 'site'
    SEAFILE = 'seafile'

    @classmethod
    def is_valid(cls, value):
        return value in {item.value for item in cls}
