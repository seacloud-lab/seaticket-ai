import copy
from enum import Enum

MODEL_USAGE_STATISTIC_CHANNEL_NAME = 'log_ai_model_usage'


DEFAULT_AGENT_AUTO_CONFIRM = {
    'suggest_notify_assignee': True,
    'suggest_create_ticket': False,
}

DEFAULT_AGENT_SETTINGS = {
    'enabled': True,
    'model': 'gemini-3-flash',
    'notify_before_due_hours': 48,
    'github_issue_type_mapping': {
        'Bug': 'Bug',
        'Feature': 'Feature',
    },
    'ticket_rules': '',
    'auto_confirm': DEFAULT_AGENT_AUTO_CONFIRM,
}


def merge_project_agent_settings_defaults(project_settings):
    if not isinstance(project_settings, dict):
        project_settings = {}

    result = copy.deepcopy(project_settings)
    result['agent'] = _merge_agent_settings_defaults(result.get('agent'))
    return result


def _merge_agent_settings_defaults(agent_settings):
    if not isinstance(agent_settings, dict):
        agent_settings = {}

    result = copy.deepcopy(DEFAULT_AGENT_SETTINGS)
    _deep_update(result, agent_settings)
    return result


def _deep_update(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
    return target


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
    GENERAL_TASK = 'general_task'
    LINEAR = 'linear'

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

    @classmethod
    def get_task_connection_types(cls):
        return [
            item.value
            for item in cls if item.value in SOURCES_TYPE.TASKS.value
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

    @classmethod
    def get_task_extra_source_types(cls):
        return [
            item.value
            for item in cls if item.value in SOURCES_TYPE.TASKS.value
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
    TASKS = (
        ConnectionType.GENERAL_TASK.value,
        ConnectionType.LINEAR.value,
    )

class ATTACHMENTS_PROMPTS(Enum):
    DOCUMENTS = 'Here are some documents in Json format with title, URL or path, and content. \n\n'
    ISSUES = 'Here are some tickets, issues or email in Json format with title, content and comments. \n\n'
    TASK = 'Here are some tasks in Json format with title and content. \n\n'

class ATTACHMENTS_TYPE(Enum):
    DOCUMENTS = SOURCES_TYPE.DOCUMENTS.value
    ISSUES = SOURCES_TYPE.ISSUES.value
    TASK = SOURCES_TYPE.TASKS.value
    OTHERS = ()

class MODEL_REASONING_TIER(Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'

    @classmethod
    def is_valid(cls, value):
        return value in {item.value for item in cls}

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
