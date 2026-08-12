from seaqa_ai.config import SEARCH_TOOL_CONTENT_MAX_SIZE, SEARCH_TOOL_ISSUES_MAX_COMMENTS
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.seadb_models.utils import get_connection_table_name
from seaqa_ai.utils import url_to_filename
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType
from seaqa_ai.utils.storage import S3Storage
from seaqa_ai.utils.tools import BasicTool
from seaqa_ai.agent.utils.record_identity import record_identity_schema


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
            connection_id = None
        return {
            'source_type': source_type,
            'connection_id': connection_id,
            'record_id': record_id,
        }

    def _load_primary_records(self, source_type, connection_id, targets):
        record_ids = [target['record_id'] for target in targets]
        fields = ', '.join(self._SELECT_FIELDS[source_type])
        return self.app.seadb_api.get_records_by_pks(
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
        contents_by_id = self.app.seadb_api.get_records_by_pks(
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
        contents_by_id = self._contents_by_id(SchemaTables.TICKETS.table_name(), records_by_id)
        comments_by_ticket_id = self._selected_entries(
            table_name=SchemaTables.TICKET_COMMENTS.table_name(),
            topic_ids=records_by_id.keys(),
            topic_id_field_name='ticket_id',
            fields_str='_pk, creator, content',
            first_order_by_field='_pk',
            latest_order_by_field='_pk',
            dedupe_key_fields=['_pk'],
            sort_key_fields=['_pk'],
            initial_content_lengths={
                record_id: len(content or '')
                for record_id, content in contents_by_id.items()
            },
            omitted_key='creator',
        )
        resolver = getattr(self.app, 'username_resolver', None)
        for record_id, record in records_by_id.items():
            record['content'] = {
                'content': self._truncate(contents_by_id.get(record_id)),
                'comments': [
                    {
                        'creator': self._resolve_ticket_user(comment.get('creator'), resolver),
                        'content': comment.get('content') or '',
                    }
                    for comment in comments_by_ticket_id.get(record_id, [])
                ],
            }

    def _load_github_issue_content(self, connection_id, records_by_id):
        contents_by_id = self._contents_by_id(
            SchemaTables.GITHUB_ISSUES.table_name(connection_id), records_by_id,
        )
        issue_id_to_record_id = {
            record['issue_id']: record_id for record_id, record in records_by_id.items()
        }
        comments_by_issue_id = self._selected_entries(
            table_name=SchemaTables.GITHUB_ISSUE_COMMENTS.table_name(connection_id),
            topic_ids=issue_id_to_record_id,
            topic_id_field_name='issue_id',
            fields_str='comment_id, author, content',
            has_deleted_field=False,
            first_order_by_field='comment_id',
            latest_order_by_field='comment_id',
            dedupe_key_fields=['comment_id'],
            sort_key_fields=['comment_id'],
            initial_content_lengths={
                issue_id: len(contents_by_id.get(record_id) or '')
                for issue_id, record_id in issue_id_to_record_id.items()
            },
            omitted_key='author',
        )
        for record_id, record in records_by_id.items():
            comments = comments_by_issue_id.get(record['issue_id'], [])
            record['content'] = {
                'content': self._truncate(contents_by_id.get(record_id)),
                'comments': [
                    {'author': comment.get('author'), 'content': comment.get('content') or ''}
                    for comment in comments
                ],
            }

    def _load_discourse_forum_content(self, connection_id, records_by_id):
        topic_id_to_record_id = {
            record['topic_id']: record_id for record_id, record in records_by_id.items()
        }
        replies_by_topic_id = self._selected_entries(
            table_name=SchemaTables.DISCOURSE_REPLIES.table_name(connection_id),
            topic_ids=topic_id_to_record_id,
            topic_id_field_name='topic_id',
            fields_str='author, post_number, content',
            has_deleted_field=False,
            first_order_by_field='post_number',
            latest_order_by_field='post_number',
            dedupe_key_fields=['post_number'],
            sort_key_fields=['post_number'],
            initial_content_lengths={topic_id: 0 for topic_id in topic_id_to_record_id},
            omitted_key='author',
            omitted_extra={'post_number': None},
        )
        for record in records_by_id.values():
            record['content'] = {
                'comments': [
                    {
                        'author': reply.get('author'),
                        'post_number': reply.get('post_number'),
                        'content': reply.get('content') or '',
                    }
                    for reply in replies_by_topic_id.get(record['topic_id'], [])
                ],
            }

    def _load_email_content(self, connection_id, records_by_id):
        emails_by_thread_id = self._selected_entries(
            table_name=SchemaTables.EMAIL.table_name(connection_id),
            topic_ids=records_by_id.keys(),
            topic_id_field_name='thread_id',
            fields_str='title, email_from, email_to, cc, content, message_id, modified_time',
            first_order_by_field='modified_time',
            latest_order_by_field='modified_time',
            dedupe_key_fields=['message_id', 'modified_time', 'email_from', 'email_to', 'content'],
            sort_key_fields=['modified_time', 'message_id'],
            initial_content_lengths={record_id: 0 for record_id in records_by_id},
            omitted_key='title',
            omitted_extra={
                'email_from': None, 'email_to': None, 'cc': None,
                'message_id': None, 'modified_time': None,
            },
        )
        for record_id, record in records_by_id.items():
            record['content'] = {
                'emails': [
                    {
                        'title': email.get('title'),
                        'email_from': email.get('email_from'),
                        'email_to': email.get('email_to'),
                        'cc': email.get('cc'),
                        'content': email.get('content') or '',
                    }
                    for email in emails_by_thread_id.get(record_id, [])
                ],
            }

    def _load_linear_content(self, connection_id, records_by_id):
        contents_by_id = self._contents_by_id(
            SchemaTables.LINEAR_ISSUES.table_name(connection_id), records_by_id,
        )
        issue_id_to_record_id = {
            record['issue_id']: record_id for record_id, record in records_by_id.items()
        }
        comments_by_issue_id = self._selected_entries(
            table_name=SchemaTables.LINEAR_ISSUE_COMMENTS.table_name(connection_id),
            topic_ids=issue_id_to_record_id,
            topic_id_field_name='issue_id',
            fields_str='comment_id, author, content, created_time',
            has_deleted_field=False,
            first_order_by_field='created_time',
            latest_order_by_field='created_time',
            dedupe_key_fields=['comment_id'],
            sort_key_fields=['created_time'],
            initial_content_lengths={
                issue_id: len(contents_by_id.get(record_id) or '')
                for issue_id, record_id in issue_id_to_record_id.items()
            },
            omitted_key='author',
            omitted_extra={'created_time': None},
        )
        for record_id, record in records_by_id.items():
            comments = comments_by_issue_id.get(record['issue_id'], [])
            record['content'] = {
                'content': self._truncate(contents_by_id.get(record_id)),
                'comments': [
                    {
                        'author': comment.get('author'),
                        'created_time': comment.get('created_time'),
                        'content': comment.get('content') or '',
                    }
                    for comment in comments
                ],
            }

    def _load_discord_content(self, connection_id, records_by_id):
        thread_id_to_record_id = {
            record['thread_id']: record_id for record_id, record in records_by_id.items()
        }
        messages_by_thread_id = self._selected_entries(
            table_name=SchemaTables.DISCORD_THREAD_MESSAGES.table_name(connection_id),
            topic_ids=thread_id_to_record_id,
            topic_id_field_name='thread_id',
            fields_str='message_id, author, content, created_time',
            has_deleted_field=False,
            first_order_by_field='created_time',
            latest_order_by_field='created_time',
            dedupe_key_fields=['message_id'],
            sort_key_fields=['created_time', 'message_id'],
            initial_content_lengths={thread_id: 0 for thread_id in thread_id_to_record_id},
            omitted_key='author',
            omitted_extra={'created_time': None},
        )
        for record in records_by_id.values():
            record['content'] = {
                'messages': [
                    {
                        'author': message.get('author'),
                        'created_time': message.get('created_time'),
                        'content': message.get('content') or '',
                    }
                    for message in messages_by_thread_id.get(record['thread_id'], [])
                ],
            }

    def _contents_by_id(self, table_name, records_by_id):
        return {
            record_id: record.get('content')
            for record_id, record in self.app.seadb_api.get_records_by_pks(
                base_id=self.project_uuid,
                table_name=table_name,
                pks=records_by_id.keys(),
                fields_str='content',
            ).items()
        }

    def _selected_entries(self, *, table_name, topic_ids, topic_id_field_name, fields_str,
                          first_order_by_field, latest_order_by_field, dedupe_key_fields,
                          sort_key_fields, initial_content_lengths, omitted_key,
                          has_deleted_field=True, omitted_extra=None):
        omitted_extra = omitted_extra or {}

        def omitted_notice():
            return {omitted_key: None, 'content': '[comments omitted due to content limit]', **omitted_extra}

        def more_omitted_notice():
            return {omitted_key: None, 'content': '[more comments omitted due to content limit]', **omitted_extra}

        return self.app.seadb_api.get_selected_topic_entries(
            base_id=self.project_uuid,
            table_name=table_name,
            topic_ids=topic_ids,
            topic_id_field_name=topic_id_field_name,
            fields_str=fields_str,
            has_deleted_field=has_deleted_field,
            limit_for_each_topic=SEARCH_TOOL_ISSUES_MAX_COMMENTS,
            first_order_by_field=first_order_by_field,
            latest_order_by_field=latest_order_by_field,
            dedupe_key_fields=dedupe_key_fields,
            sort_key_fields=sort_key_fields,
            initial_content_lengths=initial_content_lengths,
            max_total_content_size=SEARCH_TOOL_CONTENT_MAX_SIZE,
            omitted_notice_builder=omitted_notice,
            more_omitted_notice_builder=more_omitted_notice,
        )

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
            metadata = self.app.seadb_api.get_base_metadata(self.project_uuid)
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
