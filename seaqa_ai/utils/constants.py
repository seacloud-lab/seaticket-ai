from enum import Enum

MODEL_USAGE_STATISTIC_CHANNEL_NAME = 'log_ai_model_usage'

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
    
class NonConnectionType(Enum):
    KNOWLEDGE_BASE = 'knowledge_base'
    TICKET = 'ticket'

class ATTACHMENTS_PROMPTS(Enum):
    DOCUMENTS = 'Here are some documents in Json format with title, URL or path, and content. \n\n'
    ISSUES = 'Here are some tickets, issues or email in Json format with title, content and comments. \n\n'

class ATTACHMENTS_TYPE(Enum):
    DOCUMENTS = (
        NonConnectionType.KNOWLEDGE_BASE.value,
        ConnectionType.SITE.value,
        ConnectionType.SEAFILE.value
    )
    ISSUES = (
        NonConnectionType.TICKET.value,
        ConnectionType.GITHUB_ISSUE.value,
        ConnectionType.DISCOURSE_FORUM.value,
        ConnectionType.EMAIL.value
    )
