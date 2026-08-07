from seaqa_ai.db.models import ProjectConnection
from seaqa_ai.seadb_models.utils import get_connection_table_name
from seaqa_ai.utils import uuid_str_to_32_chars
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
                    'description': 'The record source type.',
                },
                'connection_id': {
                    'type': ['integer', 'null'],
                    'description': (
                        'The connection ID for connection-backed records. '
                        'Use null for tickets.'
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
        'ticket': (
            '_pk', 'title', 'content', 'state', 'substate', 'type', 'tags',
            'assignees', 'participants', 'linked_connection_records', 'priority',
            'creator', 'comment_count', 'created_time', 'modified_time',
            'closed_time', 'due_date', 'last_agent_processed_at',
        ),
        'github_issue': (
            '_pk', 'issue_id', 'issue_number', 'title', 'content', 'state',
            'state_reason', 'labels', 'issue_type', 'author', 'assignees', 'url',
            'created_time', 'modified_time', 'closed_time', 'comment_count',
            'linked_ticket',
        ),
        'discourse_forum': (
            '_pk', 'topic_id', 'title', 'slug', 'views', 'category_id',
            'created_time', 'modified_time', 'resolved', 'linked_ticket',
        ),
        'email': (
            '_pk', 'title', 'modified_time', 'unread', 'tags', 'linked_ticket',
        ),
        'general_task': (
            '_pk', 'source_task_id', 'url', 'title', 'status', 'size', 'priority',
            'assignees', 'participants', 'version', 'others', 'content', 'due_date',
            'modified_time', 'created_time', 'linked_ticket',
        ),
    }
    _SUPPORTED_SOURCE_TYPES = frozenset(_SELECT_FIELDS)
    _CONNECTION_REQUIRED_SOURCE_TYPES = _SUPPORTED_SOURCE_TYPES - {'ticket'}

    @classmethod
    def _build_select_fields(cls, source_type):
        return ', '.join(f'`{field}`' for field in cls._SELECT_FIELDS[source_type])

    @classmethod
    def _build_where_clause(cls, source_type, record_id):
        # All supported primary tables use the same soft-delete convention.
        cls._SELECT_FIELDS[source_type]
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
    def _validate_connection(app, project_uuid, source_type, connection_id):
        with app.db_session_class() as db_session:
            connections = ProjectConnection.get_connections_by_ids(
                db_session,
                uuid_str_to_32_chars(project_uuid),
                [connection_id],
            )

        if not connections or connections[0]['type'] != source_type:
            raise ValueError(
                f'Connection {connection_id} is not an active {source_type!r} '
                f'connection in this project'
            )

    @classmethod
    def _build_query(cls, source_type, connection_id, record_id):
        table_name = get_connection_table_name(source_type, connection_id)
        return (
            f'SELECT {cls._build_select_fields(source_type)} '
            f'FROM `{table_name}` '
            f'WHERE {cls._build_where_clause(source_type, record_id)} '
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

    def execute(self, source_type, connection_id, record_id, project_uuid, app, record_cache=None):
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
            self._validate_connection(app, project_uuid, source_type, connection_id)

        sql = self._build_query(source_type, connection_id, record_id)
        results = app.seadb_api.query_rows(project_uuid, sql).get('results', [])
        if not results:
            raise ValueError(
                f'No active {source_type!r} record found with record_id {record_id}'
            )
        record = results[0]
        if source_type == 'ticket':
            record = self._format_ticket_users(record, getattr(app, 'username_resolver', None))
        elif source_type == 'github_issue':
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
