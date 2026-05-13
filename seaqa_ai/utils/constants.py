from enum import Enum

MODEL_USAGE_STATISTIC_CHANNEL_NAME = 'log_ai_model_usage'


LANGUAGE = {
    'en': 'English',
    'zh-cn': 'Chinese',
    'fr' : 'French',
    'de' : 'German',
}


class ConnectionType(Enum):
    EMAIL = 'email'
    GITHUB_ISSUE = 'github_issue'
    DISCOURSE_FORUM = 'discourse_forum'
    SITE = 'site'
    SEAFILE = 'seafile'

    @classmethod
    def is_valid(cls, value):
        return value in {item.value for item in cls}
    
    @classmethod
    def get_document_connection_types(cls):
        return [
            item.value
            for item in cls if item.value in SOURCES_TYPE.DOCUMENTS.value
        ]
    
    @classmethod
    def get_issue_connection_types(cls):
        return [
            item.value
            for item in cls if item.value in SOURCES_TYPE.ISSUES.value
        ]

class ExtraSourceType(Enum):
    KNOWLEDGE_BASE = 'knowledge_base'
    TICKET = 'ticket'

    @classmethod
    def is_valid(cls, value):
        return value in {item.value for item in cls}

    @classmethod
    def get_document_extra_source_types(cls):
        return [
            item.value
            for item in cls if item.value in SOURCES_TYPE.DOCUMENTS.value
        ]
    
    @classmethod
    def get_issue_extra_source_types(cls):
        return [
            item.value
            for item in cls if item.value in SOURCES_TYPE.ISSUES.value
        ]

class SOURCES_TYPE(Enum):
    DOCUMENTS = (
        ExtraSourceType.KNOWLEDGE_BASE.value,
        ConnectionType.SITE.value,
        ConnectionType.SEAFILE.value
    )
    ISSUES = (
        ExtraSourceType.TICKET.value,
        ConnectionType.GITHUB_ISSUE.value,
        ConnectionType.DISCOURSE_FORUM.value,
        ConnectionType.EMAIL.value
    )

class ATTACHMENTS_PROMPTS(Enum):
    DOCUMENTS = 'Here are some documents in Json format with title, URL or path, and content. \n\n'
    ISSUES = 'Here are some tickets, issues or email in Json format with title, content and comments. \n\n'

class ATTACHMENTS_TYPE(Enum):
    DOCUMENTS = SOURCES_TYPE.DOCUMENTS.value
    ISSUES = SOURCES_TYPE.ISSUES.value
    OTHERS = ()

class MODEL_REASONING_TIER(Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'

    @classmethod
    def get_level(cls, tier):
        if tier.lower() == cls.LOW.value:
            return 1
        elif tier.lower() == cls.MEDIUM.value:
            return 2
        elif tier.lower() == cls.HIGH.value:
            return 3
        return 0

class TICKET_TABLES(Enum):
    TICKETS_TABLE = 'tickets'
    TICKET_COMMENTS_TABLE = 'ticket_comments'
    TICKET_ACTIVITIES_TABLE = 'ticket_activities'


class AIScenario(Enum):
    SUMMARY = 'summary'
    AGENT = 'agent'
    CHAT = 'chat'
    PORTAL_CHAT = 'portal-chat'
    SEARCH = 'search'
    RECORD_GENERATION = 'record-generation'
    UNKNOWN = 'unknown'

    @classmethod
    def is_valid(cls, value):
        return value in {item.value for item in cls}
