from seaqa_ai.config import SEARCH_TOOL_CONTENT_MAX_SIZE, SEARCH_TOOL_ISSUES_MAX_COMMENTS
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.seadb_models.utils import get_connection_table_name
from seaqa_ai.utils import url_to_filename
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType
from seaqa_ai.utils.storage import S3Storage
from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.utils.seadb import ContentBudget, ContentPriority, ticket_comments_query, \
    github_issue_comments_query, discourse_replies_query, email_messages_query, \
    linear_issue_comments_query, discord_thread_messages_query
from seaqa_ai.agent.utils.record_identity import (
    record_identity_schema,
    validate_record_identity,
)

AGENT_CONTENT_BUDGET = ContentBudget(max_chars=SEARCH_TOOL_CONTENT_MAX_SIZE, priority=ContentPriority.LATEST_MESSAGE_FIRST)
MESSAGES_OMITTED_NOTICE = '[messages omitted due to content limit]'


class _RecordRetriever:
    """Batch-load records and their agent-specific content."""

    _SELECT_FIELDS = {
        ExtraSourceType.TICKET.value: (
            '_pk', 'title', 'state', 'substate', 'type', 'tags', 'assignees',
            'participants', 'linked_connection_records', 'priority', 'creator',
            'comment_count', 'created_time', 'modified_time', 'closed_time',
            'due_date', 'last_agent_processed_at',
        ),
        ExtraSourceType.KNOWLEDGE_BASE.value: (
            '_pk', 'title', 'tags', 'creator', 'created_time', 'last_modifier',
            'modified_time',
        ),
        ConnectionType.SITE.value: ('_pk', 'url', 'title', 'modified_time'),
        ConnectionType.SEAFILE.value: ('_pk', 'file_id', 'path', 'title', 'modified_time'),
        ConnectionType.GITHUB_ISSUE.value: (
            '_pk', 'issue_id', 'issue_number', 'title', 'state', 'state_reason',
            'labels', 'issue_type', 'author', 'assignees', 'url', 'created_time',
            'modified_time', 'closed_time', 'comment_count', 'linked_ticket',
        ),
        ConnectionType.DISCOURSE_FORUM.value: (
            '_pk', 'topic_id', 'title', 'slug', 'views', 'category_id', 'created_time',
            'modified_time', 'resolved', 'linked_ticket',
        ),
        ConnectionType.EMAIL.value: (
            '_pk', 'title', 'modified_time', 'unread', 'tags', 'linked_ticket',
        ),
        ConnectionType.GENERAL_TASK.value: (
            '_pk', 'source_task_id', 'url', 'title', 'status', 'size', 'priority',
            'assignees', 'participants', 'version', 'others', 'due_date',
            'modified_time', 'created_time', 'linked_ticket',
        ),
        ConnectionType.LINEAR.value: (
            '_pk', 'issue_id', 'identifier', 'title', 'state', 'labels', 'author',
            'assignees', 'priority', 'due_date', 'created_time', 'modified_time',
            'closed_time', 'linked_ticket',
        ),
        ConnectionType.DISCORD.value: (
            '_pk', 'thread_id', 'title', 'created_time', 'modified_time', 'linked_ticket',
        ),
    }
    _SUPPORTED_SOURCE_TYPES = frozenset(_SELECT_FIELDS)
    _CONNECTION_REQUIRED_SOURCE_TYPES = frozenset(
        source_type for source_type in _SUPPORTED_SOURCE_TYPES
        if not ExtraSourceType.is_valid(source_type)
    )

    def __init__(self, app, project_uuid, context=None, record_cache=None):
        self.app = app
        self.project_uuid = project_uuid
        self.context = context or {}
        self.record_cache = record_cache

    def retrieve(self, targets):
        """Load records for targets, preserving their input order."""
        if not isinstance(targets, list):
            raise ValueError('targets must be an array')
        normalized_targets = [self._normalize_target(target) for target in targets]
        records_by_key = {}
        targets_by_source = {}

        for target in normalized_targets:
            cache_key = self._cache_key(**target)
            if self.record_cache is not None and cache_key in self.record_cache:
                records_by_key[cache_key] = self.record_cache[cache_key]
                continue
            targets_by_source.setdefault(
                (target['source_type'], target['connection_id']), [],
            ).append(target)

        for (source_type, connection_id), source_targets in targets_by_source.items():
            loaded_records = self._load_primary_records(source_type, connection_id, source_targets)
            self._load_content(source_type, connection_id, loaded_records)
            if source_type == ExtraSourceType.TICKET.value:
                loaded_records = {
                    record_id: self._format_ticket_users(
                        record, getattr(self.app, 'username_resolver', None),
                    )
                    for record_id, record in loaded_records.items()
                }
            elif source_type == ConnectionType.GITHUB_ISSUE.value:
                self._add_github_available_labels(connection_id, loaded_records)

            for record_id, record in loaded_records.items():
                cache_key = self._cache_key(source_type, connection_id, record_id)
                records_by_key[cache_key] = record
                if self.record_cache is not None:
                    self.record_cache[cache_key] = record

        results = []
        for target in normalized_targets:
            cache_key = self._cache_key(**target)
            record = records_by_key.get(cache_key)
            if record is None:
                raise ValueError(
                    f"No active {target['source_type']!r} record found with record_id "
                    f"{target['record_id']}"
                )
            results.append(record)
        return results

    def _normalize_target(self, target):
        if not isinstance(target, dict):
            raise ValueError('target must be an object')
        source_type = str(target.get('source_type') or '').strip()
        if source_type not in self._SUPPORTED_SOURCE_TYPES:
            available_source_types = ', '.join(sorted(self._SUPPORTED_SOURCE_TYPES))
            raise ValueError(
                f'Unsupported source type: {source_type}. '
                f'Available source types: {available_source_types}'
            )
        record_id = self._parse_id(target.get('record_id'), 'record_id')
        if source_type in self._CONNECTION_REQUIRED_SOURCE_TYPES:
            connection_id = self._parse_id(target.get('connection_id'), 'connection_id')
            self._validate_connection(source_type, connection_id)
        else:
            connection_id = target.get('connection_id')
        validate_record_identity(source_type, connection_id, record_id)
        return {
            'source_type': source_type,
            'connection_id': connection_id,
            'record_id': record_id,
        }

    def _load_primary_records(self, source_type, connection_id, targets):
        record_ids = [target['record_id'] for target in targets]
        fields = ', '.join(self._SELECT_FIELDS[source_type])
        return self.app.seadb_service.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=get_connection_table_name(source_type, connection_id),
            pks=record_ids,
            fields_str=fields,
        )

    def _load_content(self, source_type, connection_id, records_by_id):
        method = getattr(self, f'_load_{source_type}_content')
        method(connection_id, records_by_id)

    def _load_knowledge_base_content(self, _, records_by_id):
        self._load_plain_content(SchemaTables.KNOWLEDGE_BASE.table_name(), records_by_id)

    def _load_seafile_content(self, connection_id, records_by_id):
        self._load_plain_content(SchemaTables.SEAFILE.table_name(connection_id), records_by_id)

    def _load_general_task_content(self, connection_id, records_by_id):
        self._load_plain_content(SchemaTables.GENERAL_TASK.table_name(connection_id), records_by_id)

    def _load_plain_content(self, table_name, records_by_id):
        contents_by_id = self.app.seadb_service.get_records_by_pks(
            base_id=self.project_uuid,
            table_name=table_name,
            pks=records_by_id.keys(),
            fields_str='content',
        )
        for record_id, record in records_by_id.items():
            record['content'] = self._truncate(contents_by_id.get(record_id, {}).get('content'))

    def _load_site_content(self, connection_id, records_by_id):
        from seaqa_ai.config import S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET

        with S3Storage(S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET) as storage:
            for record in records_by_id.values():
                page = storage.read_json(
                    site_id=connection_id,
                    project_uuid=self.project_uuid,
                    filename=url_to_filename(record['url']),
                ) or {}
                record['content'] = self._truncate(page.get('content'))

    def _load_ticket_content(self, _, records_by_id):
        bodies_by_ticket_id = self._contents_by_id(SchemaTables.TICKETS.table_name(), records_by_id)
        content_by_ticket_id = self.app.seadb_service.get_thread_content(
            self.project_uuid,
            records_by_id.keys(),
            query=ticket_comments_query(SchemaTables.TICKET_COMMENTS.table_name(), SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            bodies_by_thread_id=bodies_by_ticket_id,
            budget=AGENT_CONTENT_BUDGET,
        )
        resolver = getattr(self.app, 'username_resolver', None)
        for record_id, record in records_by_id.items():
            ticket_content = content_by_ticket_id[record_id]
            history_comments = [
                self._format_ticket_comment(message, resolver)
                for message in ticket_content.history_messages
            ]
            if ticket_content.has_omitted_messages:
                history_comments.append({'creator': None, 'content': MESSAGES_OMITTED_NOTICE})
            record['content'] = {
                'latest_comment': self._format_ticket_comment(ticket_content.latest_message, resolver),
                'content': ticket_content.body,
                'history_comments': history_comments,
            }

    def _load_github_issue_content(self, connection_id, records_by_id):
        contents_by_id = self._contents_by_id(
            SchemaTables.GITHUB_ISSUES.table_name(connection_id), records_by_id,
        )
        bodies_by_issue_id = {
            record['issue_id']: contents_by_id.get(record_id)
            for record_id, record in records_by_id.items()
        }
        content_by_issue_id = self.app.seadb_service.get_thread_content(
            self.project_uuid,
            bodies_by_issue_id.keys(),
            query=github_issue_comments_query(SchemaTables.GITHUB_ISSUE_COMMENTS.table_name(connection_id), SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            bodies_by_thread_id=bodies_by_issue_id,
            budget=AGENT_CONTENT_BUDGET,
        )
        for record_id, record in records_by_id.items():
            issue_content = content_by_issue_id[record['issue_id']]
            history_comments = [
                self._format_message(message, ('author', 'content'))
                for message in issue_content.history_messages
            ]
            if issue_content.has_omitted_messages:
                history_comments.append({'author': None, 'content': MESSAGES_OMITTED_NOTICE})
            record['content'] = {
                'latest_comment': self._format_message(issue_content.latest_message, ('author', 'content')),
                'content': issue_content.body,
                'history_comments': history_comments,
            }

    def _load_discourse_forum_content(self, connection_id, records_by_id):
        content_by_topic_id = self.app.seadb_service.get_thread_content(
            self.project_uuid,
            [record['topic_id'] for record in records_by_id.values()],
            query=discourse_replies_query(SchemaTables.DISCOURSE_REPLIES.table_name(connection_id), SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            budget=AGENT_CONTENT_BUDGET,
        )
        for record in records_by_id.values():
            topic_content = content_by_topic_id[record['topic_id']]
            history_comments = [
                self._format_message(message, ('author', 'post_number', 'content'))
                for message in topic_content.history_messages
            ]
            if topic_content.has_omitted_messages:
                history_comments.append({
                    'author': None, 'post_number': None, 'content': MESSAGES_OMITTED_NOTICE,
                })
            record['content'] = {
                'latest_comment': self._format_message(
                    topic_content.latest_message, ('author', 'post_number', 'content'),
                ),
                'history_comments': history_comments,
            }

    def _load_email_content(self, connection_id, records_by_id):
        email_fields = ('title', 'email_from', 'email_to', 'cc', 'content')
        content_by_thread_id = self.app.seadb_service.get_thread_content(
            self.project_uuid,
            records_by_id.keys(),
            query=email_messages_query(SchemaTables.EMAIL.table_name(connection_id), SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            budget=AGENT_CONTENT_BUDGET,
        )
        for record_id, record in records_by_id.items():
            thread_content = content_by_thread_id[record_id]
            history_emails = [
                self._format_message(message, email_fields)
                for message in thread_content.history_messages
            ]
            if thread_content.has_omitted_messages:
                history_emails.append({
                    'title': None, 'email_from': None, 'email_to': None, 'cc': None,
                    'content': MESSAGES_OMITTED_NOTICE,
                })
            record['content'] = {
                'latest_email': self._format_message(thread_content.latest_message, email_fields),
                'history_emails': history_emails,
            }

    def _load_linear_content(self, connection_id, records_by_id):
        contents_by_id = self._contents_by_id(
            SchemaTables.LINEAR_ISSUES.table_name(connection_id), records_by_id,
        )
        bodies_by_issue_id = {
            record['issue_id']: contents_by_id.get(record_id)
            for record_id, record in records_by_id.items()
        }
        content_by_issue_id = self.app.seadb_service.get_thread_content(
            self.project_uuid,
            bodies_by_issue_id.keys(),
            query=linear_issue_comments_query(SchemaTables.LINEAR_ISSUE_COMMENTS.table_name(connection_id), SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            bodies_by_thread_id=bodies_by_issue_id,
            budget=AGENT_CONTENT_BUDGET,
        )
        for record_id, record in records_by_id.items():
            issue_content = content_by_issue_id[record['issue_id']]
            history_comments = [
                self._format_message(message, ('author', 'created_time', 'content'))
                for message in issue_content.history_messages
            ]
            if issue_content.has_omitted_messages:
                history_comments.append({
                    'author': None, 'created_time': None, 'content': MESSAGES_OMITTED_NOTICE,
                })
            record['content'] = {
                'latest_comment': self._format_message(
                    issue_content.latest_message, ('author', 'created_time', 'content'),
                ),
                'content': issue_content.body,
                'history_comments': history_comments,
            }

    def _load_discord_content(self, connection_id, records_by_id):
        content_by_thread_id = self.app.seadb_service.get_thread_content(
            self.project_uuid,
            [record['thread_id'] for record in records_by_id.values()],
            query=discord_thread_messages_query(SchemaTables.DISCORD_THREAD_MESSAGES.table_name(connection_id), SEARCH_TOOL_ISSUES_MAX_COMMENTS),
            budget=AGENT_CONTENT_BUDGET,
        )
        for record in records_by_id.values():
            thread_content = content_by_thread_id[record['thread_id']]
            history_messages = [
                self._format_message(message, ('author', 'created_time', 'content'))
                for message in thread_content.history_messages
            ]
            if thread_content.has_omitted_messages:
                history_messages.append({
                    'author': None, 'created_time': None, 'content': MESSAGES_OMITTED_NOTICE,
                })
            record['content'] = {
                'latest_message': self._format_message(
                    thread_content.latest_message, ('author', 'created_time', 'content'),
                ),
                'history_messages': history_messages,
            }

    def _contents_by_id(self, table_name, records_by_id):
        return {
            record_id: record.get('content')
            for record_id, record in self.app.seadb_service.get_records_by_pks(
                base_id=self.project_uuid,
                table_name=table_name,
                pks=records_by_id.keys(),
                fields_str='content',
            ).items()
        }

    @staticmethod
    def _format_message(message, fields):
        if message is None:
            return None
        formatted = {field: message.get(field) for field in fields}
        if 'content' in fields:
            formatted['content'] = message.get('content') or ''
        return formatted

    def _format_ticket_comment(self, message, resolver):
        formatted = self._format_message(message, ('creator', 'content'))
        if formatted is not None:
            formatted['creator'] = self._resolve_ticket_user(formatted['creator'], resolver)
        return formatted

    def _add_github_available_labels(self, connection_id, records_by_id):
        cache_key = ('github_available_labels', connection_id)
        if self.record_cache is not None and cache_key in self.record_cache:
            labels = self.record_cache[cache_key]
        else:
            labels = self._get_github_available_labels(connection_id)
            if self.record_cache is not None:
                self.record_cache[cache_key] = labels
        for record in records_by_id.values():
            record['available_labels'] = labels

    def _get_github_available_labels(self, connection_id):
        try:
            metadata = self.app.seadb_service.get_base_metadata(self.project_uuid)
            table_name = SchemaTables.GITHUB_ISSUES.table_name(connection_id)
            table_meta = next(
                (table for table in (metadata or {}).get('tables', []) if table.get('name') == table_name),
                None,
            )
            label_column = next(
                (column for column in (table_meta or {}).get('columns', []) if column.get('name') == 'labels'),
                None,
            )
            labels = []
            seen = set()
            for option in ((label_column or {}).get('data') or {}).get('options') or []:
                label = str(option.get('name') or '').strip()
                if label and label.casefold() not in seen:
                    seen.add(label.casefold())
                    labels.append(label)
            return labels
        except Exception:
            return []

    def _validate_connection(self, source_type, connection_id):
        connections = (
            self.context.get('document_connections', [])
            + self.context.get('issue_connections', [])
            + self.context.get('task_connections', [])
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
    def _cache_key(source_type, connection_id, record_id):
        return source_type, connection_id, record_id

    @staticmethod
    def _truncate(content):
        return (content or '')[:SEARCH_TOOL_CONTENT_MAX_SIZE]

    @staticmethod
    def _resolve_ticket_user(value, username_resolver):
        if username_resolver is None:
            return value
        return username_resolver._convert_username_like_value(value)

    @classmethod
    def _format_ticket_users(cls, record, username_resolver):
        if username_resolver is None:
            return record
        formatted_record = dict(record)
        for field in ('creator', 'assignees', 'participants'):
            if field in formatted_record:
                formatted_record[field] = cls._resolve_ticket_user(
                    formatted_record[field], username_resolver,
                )
        return formatted_record


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
        'parameters': record_identity_schema(),
        'strict': True,
    }

    def execute(self, source_type, connection_id, record_id, project_uuid, app,
                record_cache=None, context=None):
        return _RecordRetriever(
            app=app,
            project_uuid=project_uuid,
            context=context,
            record_cache=record_cache,
        ).retrieve([{
            'source_type': source_type,
            'connection_id': connection_id,
            'record_id': record_id,
        }])[0]
