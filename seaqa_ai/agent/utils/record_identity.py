"""Shared schemas and validation for agent record identities."""

from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType, SOURCES_TYPE


EXTRA_RECORD_SOURCE_TYPES = (
    ExtraSourceType.TICKET.value,
    ExtraSourceType.KNOWLEDGE_BASE.value,
)
CONNECTION_RECORD_SOURCE_TYPES = (
    ConnectionType.SITE.value,
    ConnectionType.SEAFILE.value,
    ConnectionType.GITHUB_ISSUE.value,
    ConnectionType.DISCOURSE_FORUM.value,
    ConnectionType.EMAIL.value,
    ConnectionType.GENERAL_TASK.value,
    ConnectionType.LINEAR.value,
    ConnectionType.DISCORD.value,
)
SUPPORTED_RECORD_SOURCE_TYPES = EXTRA_RECORD_SOURCE_TYPES + CONNECTION_RECORD_SOURCE_TYPES

ALL_SOURCES_TYPES = SOURCES_TYPE.DOCUMENTS.value + SOURCES_TYPE.ISSUES.value + SOURCES_TYPE.TASKS.value


def record_identity_schema(*, include_source_title=False, recive_all_sources=False):
    properties = {
        'source_type': {
            'type': 'string',
            'enum': list(ALL_SOURCES_TYPES if recive_all_sources else SUPPORTED_RECORD_SOURCE_TYPES),
            'description': 'The record source type.',
        },
        'connection_id': {
            'type': ['integer', 'null'],
            'description': (
                'Use null only for ticket and knowledge_base records. Use an integer for '
                'site, seafile, github_issue, discourse_forum, email, general_task, '
                'linear, and discord records.'
            ),
        },
        'record_id': {
            'type': 'integer',
            'description': 'The SeaDB primary key of the record.',
        },
    }
    if include_source_title:
        properties['source_title'] = {'type': 'string'}
    return {
        'type': 'object',
        'properties': properties,
        'required': ['source_type', 'connection_id', 'record_id'],
        'additionalProperties': False,
        'allOf': [
            {
                'if': {'properties': {'source_type': {'enum': list(EXTRA_RECORD_SOURCE_TYPES)}}},
                'then': {'properties': {'connection_id': {'type': 'null'}}},
                'else': {'properties': {'connection_id': {'type': 'integer'}}},
            },
        ],
    }


def validate_record_identity(source_type, connection_id, record_id):
    source_type = str(source_type or '').strip()
    if source_type not in SUPPORTED_RECORD_SOURCE_TYPES:
        available_source_types = ', '.join(SUPPORTED_RECORD_SOURCE_TYPES)
        raise ValueError(
            f'Unsupported record source type: {source_type}. '
            f'Available source types: {available_source_types}'
        )
    if isinstance(record_id, bool) or not isinstance(record_id, int):
        raise ValueError('record_id must be an integer')
    if source_type in EXTRA_RECORD_SOURCE_TYPES:
        if connection_id is not None:
            raise ValueError(f'connection_id must be null for {source_type}')
    elif isinstance(connection_id, bool) or not isinstance(connection_id, int):
        raise ValueError(f'connection_id must be an integer for {source_type}')
    return source_type
