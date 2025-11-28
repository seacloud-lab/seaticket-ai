from enum import Enum

MODEL_USAGE_STATISTIC_CHANNEL_NAME = 'log_ai_model_usage'

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

class EXTRA_TICKET_CONTENT(Enum):
    TYPE = 'ticket'
    PROMPT = 'I will provide a Json-like issue record with title, content and some replies, please help me to resolve my problem:\n\n'

class EXTRA_ISSUE_CONTENT(Enum):
    TYPE = 'ticket'
    PROMPT = 'I will provide a GitHub issue in JSON format with title, body and comments, please help me to resolve this issue:\n\n'
