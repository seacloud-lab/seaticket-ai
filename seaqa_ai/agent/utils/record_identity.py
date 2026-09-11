"""Shared schemas and validation for agent record identities."""

from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType


EXTRA_RECORD_SOURCE_TYPES = ExtraSourceType.get_document_extra_source_types() + ExtraSourceType.get_issue_extra_source_types() + ExtraSourceType.get_task_extra_source_types()
CONNECTION_RECORD_SOURCE_TYPES = ConnectionType.get_document_connection_types() + ConnectionType.get_issue_connection_types() + ConnectionType.get_task_connection_types()

ALL_SOURCES_TYPES = EXTRA_RECORD_SOURCE_TYPES + CONNECTION_RECORD_SOURCE_TYPES


def record_identity_schema(*, nested=False):
    properties = {
        'source_type': {
            'type': 'string',
            'enum': list(ALL_SOURCES_TYPES),
            'description': 'The record source type.',
        },
        'connection_id': {
            'type': ['integer', 'null'],
            'description': (
                'Use null only for ticket, portal_issue, and knowledge_base records. Use an integer for '
                'site, seafile, github_issue, discourse_forum, email, general_task, '
                'linear, and discord records.'
            ),
        },
        'record_id': {
            'type': 'integer',
            'description': 'The SeaDB primary key of the record.',
        },
    }
    schema = {
        'type': 'object',
        'properties': properties,
        'required': ['source_type', 'connection_id', 'record_id'],
        'additionalProperties': False,
    }
    if not nested:
        return schema

    def branch(source_types, connection_id_type):
        branch_properties = dict(properties)
        branch_properties['source_type'] = {
            **branch_properties['source_type'],
            'enum': list(source_types),
        }
        branch_properties['connection_id'] = {
            **branch_properties['connection_id'],
            'type': connection_id_type,
        }
        return {
            'type': 'object',
            'properties': branch_properties,
            'required': list(schema['required']),
            'additionalProperties': False,
        }

    return {
        'anyOf': [
            branch(EXTRA_RECORD_SOURCE_TYPES, 'null'),
            branch(CONNECTION_RECORD_SOURCE_TYPES, 'integer'),
        ],
    }


def validate_record_identity(source_type, connection_id, record_id):
    source_type = str(source_type or '').strip()
    if source_type not in ALL_SOURCES_TYPES:
        available_source_types = ', '.join(ALL_SOURCES_TYPES)
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
