from seaqa_ai.seadb_models.utils import get_connection_table_name
from seaqa_ai.utils.constants import ExtraSourceType, ConnectionType
from seaqa_ai.utils.tools import BasicTool


class GetRecord(BasicTool):
    """Load one active record without following its relationship fields."""

    tool = {
        'type': 'function',
        'name': 'get_record',
        'description': (
            'Load one current record by source type, connection ID, and record ID. '
            'The returned record includes its stored link fields but does not load '
            'linked records automatically.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'source_type': {
                    'type': 'string',
                    'enum': [
                        ExtraSourceType.TICKET.value,
                        ExtraSourceType.KNOWLEDGE_BASE.value,
                        ConnectionType.GITHUB_ISSUE.value,
                        ConnectionType.DISCOURSE_FORUM.value,
                        ConnectionType.EMAIL.value,
                        ConnectionType.GENERAL_TASK.value,
                    ],
                    'description': 'The record source type.',
                },
                'connection_id': {
                    'type': ['integer', 'null'],
                    'description': (
                        'The connection ID for connection-backed records. It must be an '
                        'integer for github_issue, discourse_forum, email, and general_task; '
                        'use null only for ticket and knowledge_base records.'
                    ),
                },
                'record_id': {
                    'type': 'integer',
                    'description': 'The SeaDB primary key of the record to load.',
                },
            },
            'required': ['source_type', 'connection_id', 'record_id'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    _SELECT_FIELDS = {
        ExtraSourceType.TICKET.value: (
            '_pk', 'title', 'content', 'state', 'substate', 'type', 'tags',
            'assignees', 'participants', 'linked_connection_records', 'priority',
            'creator', 'comment_count', 'created_time', 'modified_time',
            'closed_time', 'due_date', 'last_agent_processed_at',
        ),
        ExtraSourceType.KNOWLEDGE_BASE.value: (
            '_pk', 'title', 'content', 'tags', 'creator', 'created_time',
            'last_modifier', 'modified_time',
        ),
        ConnectionType.GITHUB_ISSUE.value: (
            '_pk', 'issue_id', 'issue_number', 'title', 'content', 'state',
            'state_reason', 'labels', 'issue_type', 'author', 'assignees', 'url',
            'created_time', 'modified_time', 'closed_time', 'comment_count',
            'linked_ticket',
        ),
        ConnectionType.DISCOURSE_FORUM.value: (
            '_pk', 'topic_id', 'title', 'slug', 'views', 'category_id',
            'created_time', 'modified_time', 'resolved', 'linked_ticket',
        ),
        ConnectionType.EMAIL.value: (
            '_pk', 'title', 'modified_time', 'unread', 'tags', 'linked_ticket',
        ),
        ConnectionType.GENERAL_TASK.value: (
            '_pk', 'source_task_id', 'url', 'title', 'status', 'size', 'priority',
            'assignees', 'participants', 'version', 'others', 'content', 'due_date',
            'modified_time', 'created_time', 'linked_ticket',
        ),
    }
    _SUPPORTED_SOURCE_TYPES = frozenset(_SELECT_FIELDS)
    _CONNECTION_REQUIRED_SOURCE_TYPES = frozenset(
        source_type
        for source_type in _SUPPORTED_SOURCE_TYPES
        if not ExtraSourceType.is_valid(source_type)
    )

    @classmethod
    def _build_select_fields(cls, source_type):
        return ', '.join(f'`{field}`' for field in cls._SELECT_FIELDS[source_type])

    @classmethod
    def _build_where_clause(cls, record_id):
        # All supported primary tables use the same soft-delete convention.
        return (
            f'`_pk` = {record_id} '
            'AND (`deleted` = False OR `deleted` IS NULL)'
        )

    @staticmethod
    def _parse_id(value, field_name):
        if isinstance(value, bool):
            raise ValueError(f'{field_name} must be an integer')
        try:
            parsed_value = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f'{field_name} must be an integer') from error
        if parsed_value < 0:
            raise ValueError(f'{field_name} must be a non-negative integer')
        return parsed_value

    @staticmethod
    def _validate_connection(context, source_type, connection_id):
        context = context or {}
        connections = (
            context.get('document_connections', [])
            + context.get('issue_connections', [])
            + context.get('task_connections', [])
        )
        if not any(
            isinstance(connection, dict)
            and connection.get('id') == connection_id
            and connection.get('type') == source_type
            for connection in connections
        ):
            raise ValueError(
                f'Connection {connection_id} is not an allowed {source_type!r} '
                'connection in this context'
            )

    @classmethod
    def _build_query(cls, source_type, connection_id, record_id):
        table_name = get_connection_table_name(source_type, connection_id)
        return (
            f'SELECT {cls._build_select_fields(source_type)} '
            f'FROM `{table_name}` '
            f'WHERE {cls._build_where_clause(record_id)} '
            'LIMIT 1'
        )

    @staticmethod
    def _format_ticket_users(record, username_resolver):
        """Replace internal ticket user identifiers with display names for the agent."""
        if username_resolver is None:
            return record

        formatted_record = dict(record)
        for field in ('creator', 'assignees', 'participants'):
            if field in formatted_record:
                formatted_record[field] = username_resolver._convert_username_like_value(
                    formatted_record[field]
                )
        return formatted_record

    @staticmethod
    def _cache_key(source_type, connection_id, record_id):
        return source_type, connection_id, record_id

    @staticmethod
    def _github_labels_cache_key(connection_id):
        return 'github_available_labels', connection_id

    @staticmethod
    def _get_github_available_labels(app, project_uuid, table_name):
        try:
            metadata = app.seadb_api.get_base_metadata(project_uuid)
            table_meta = next(
                (table for table in (metadata or {}).get('tables', []) if table.get('name') == table_name),
                None,
            )
            label_column = next(
                (column for column in (table_meta or {}).get('columns', []) if column.get('name') == 'labels'),
                None,
            )
            available_labels = []
            seen = set()
            for option in ((label_column or {}).get('data') or {}).get('options') or []:
                label = str(option.get('name') or '').strip()
                normalized_label = label.casefold()
                if label and normalized_label not in seen:
                    seen.add(normalized_label)
                    available_labels.append(label)
            return available_labels
        except Exception:
            return []

    def execute(self, source_type, connection_id, record_id, project_uuid, app, record_cache=None, context=None):
        source_type = str(source_type or '').strip()
        if source_type not in self._SUPPORTED_SOURCE_TYPES:
            available_source_types = ', '.join(sorted(self._SUPPORTED_SOURCE_TYPES))
            raise ValueError(
                f'Unsupported source type: {source_type}. '
                f'Available source types: {available_source_types}'
            )

        record_id = self._parse_id(record_id, 'record_id')
        if source_type in self._CONNECTION_REQUIRED_SOURCE_TYPES:
            connection_id = self._parse_id(connection_id, 'connection_id')
        else:
            connection_id = None

        cache_key = self._cache_key(source_type, connection_id, record_id)
        if record_cache is not None and cache_key in record_cache:
            return record_cache[cache_key]

        if source_type in self._CONNECTION_REQUIRED_SOURCE_TYPES:
            self._validate_connection(context, source_type, connection_id)

        sql = self._build_query(source_type, connection_id, record_id)
        results = app.seadb_api.query_rows(project_uuid, sql).get('results', [])
        if not results:
            raise ValueError(
                f'No active {source_type!r} record found with record_id {record_id}'
            )
        record = results[0]
        if source_type == ExtraSourceType.TICKET.value:
            record = self._format_ticket_users(record, getattr(app, 'username_resolver', None))
        elif source_type == ConnectionType.GITHUB_ISSUE.value:
            record = dict(record)
            labels_cache_key = self._github_labels_cache_key(connection_id)
            if record_cache is not None and labels_cache_key in record_cache:
                record['available_labels'] = record_cache[labels_cache_key]
            else:
                record['available_labels'] = self._get_github_available_labels(
                    app, project_uuid, get_connection_table_name(source_type, connection_id),
                )
                if record_cache is not None:
                    record_cache[labels_cache_key] = record['available_labels']
        if record_cache is not None:
            record_cache[cache_key] = record
        return record
