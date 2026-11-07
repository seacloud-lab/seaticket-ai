import json
import logging
import re
import uuid

logger = logging.getLogger(__name__)

# Portal customers can open these pages with their own external accounts.
_CITABLE_SOURCE_SPECS = {
    'site': {
        'table': 'WEB_CRAWL',
        'fields': 'url',
    },
    'github_issue': {
        'table': 'GITHUB_ISSUES',
        'fields': 'url',
    },
    'discourse_forum': {
        'table': 'DISCOURSE_TOPICS',
        'fields': 'slug, topic_id',
        'needs_config': True,
    },
    'discord': {
        'table': 'DISCORD_THREADS',
        'fields': 'thread_id',
        'needs_config': True,
    },
    'notion': {
        'table': 'NOTION',
        'fields': 'page_id',
    },
    'confluence': {
        'table': 'CONFLUENCE',
        'fields': 'page_id',
        'needs_config': True,
    },
}

_REFERENCE_PATTERN = r'\[Reference {number}\](?!\d)'


def prepare_portal_citations(answer, sources, seadb_service, db_session_class, project_uuid):
    """Keep only citations a portal customer can open, and renumber the rest."""
    sources = list(sources or [])
    if not sources:
        return answer or '', []

    records_by_source, configs = _load_citation_records(
        sources, seadb_service, db_session_class, project_uuid,
    )
    public_urls = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict) or source.get('type') not in _CITABLE_SOURCE_SPECS:
            public_urls.append('')
            continue
        public_urls.append(public_url_for_source(
            source,
            records_by_source.get(index) or {},
            configs.get(_connection_id(source)) or {},
        ))
    return retain_public_citations(answer, sources, public_urls)


def public_url_for_source(source, record, config):
    """Build a customer-openable URL. Empty string means the source must not be cited."""
    if not isinstance(source, dict):
        return ''
    source_type = source.get('type')
    record = record if isinstance(record, dict) else {}
    config = config if isinstance(config, dict) else {}

    if source_type == 'site':
        return _http_url(record.get('url') or source.get('url'))
    if source_type == 'github_issue':
        return _http_url(record.get('url'))
    if source_type == 'discourse_forum':
        base_url = str(config.get('url') or '').strip().rstrip('/')
        slug = record.get('slug')
        topic_id = record.get('topic_id')
        if not base_url or not slug or topic_id in (None, ''):
            return ''
        return _http_url(f'{base_url}/t/{slug}/{topic_id}')
    if source_type == 'discord':
        guild_id = config.get('guild_id')
        thread_id = record.get('thread_id')
        if not guild_id or not thread_id:
            return ''
        return f'https://discord.com/channels/{guild_id}/{thread_id}'
    if source_type == 'notion':
        page_id = str(record.get('page_id') or '').replace('-', '')
        if not page_id:
            return ''
        return f'https://www.notion.so/{page_id}'
    if source_type == 'confluence':
        workspace_url = str(config.get('workspace_url') or '').strip().rstrip('/')
        page_id = record.get('page_id')
        if not workspace_url or page_id in (None, ''):
            return ''
        return _http_url(f'{workspace_url}/wiki/pages/viewpage.action?pageId={page_id}')
    return ''


def retain_public_citations(answer, sources, public_urls):
    """Drop sources without a public URL and renumber surviving [Reference n] markers."""
    answer = answer or ''
    sources = list(sources or [])
    if len(public_urls) != len(sources):
        public_urls = [''] * len(sources)

    for index in reversed(range(len(sources))):
        public_url = public_urls[index]
        if public_url:
            sources[index] = _public_source_payload(sources[index], public_url)
            continue
        answer = re.sub(_REFERENCE_PATTERN.format(number=index + 1), '', answer)
        for later_index in range(index + 1, len(sources)):
            answer = re.sub(
                _REFERENCE_PATTERN.format(number=later_index + 1),
                f'[Reference {later_index}]',
                answer,
            )
        del sources[index]
    return answer, sources


def _public_source_payload(source, url):
    source = source if isinstance(source, dict) else {}
    return {
        'type': source.get('type') or '',
        'title': source.get('title') or '',
        'url': url,
        'ai_summary': source.get('ai_summary') or '',
        '_id': source.get('_id'),
        'connection_id': source.get('connection_id'),
        'modified_time': source.get('modified_time') or '',
    }


def _load_citation_records(sources, seadb_service, db_session_class, project_uuid):
    """Load citation records by source index and the required connection configs."""
    grouped_ids, config_ids = _group_citation_record_ids(sources)
    rows_by_group = _load_grouped_citation_records(grouped_ids, seadb_service, project_uuid)
    records_by_source = _map_citation_records_to_sources(sources, rows_by_group)
    configs = _load_connection_configs(db_session_class, project_uuid, config_ids)
    return records_by_source, configs


def _group_citation_record_ids(sources):
    """Deduplicate record IDs by source type and connection, collecting config IDs."""
    grouped_ids = {}
    config_ids = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        spec = _CITABLE_SOURCE_SPECS.get(source.get('type'))
        connection_id = _connection_id(source)
        record_id = _record_id(source)
        if not spec or connection_id is None or record_id is None:
            continue
        grouped_ids.setdefault((source.get('type'), connection_id), set()).add(record_id)
        if spec.get('needs_config'):
            config_ids.add(connection_id)
    return grouped_ids, config_ids


def _load_grouped_citation_records(grouped_ids, seadb_service, project_uuid):
    """Query each group once; a failed group must not prevent loading the others."""
    if not seadb_service or not project_uuid or not grouped_ids:
        return {}

    base_id = _project_uuid_32(project_uuid)
    rows_by_group = {}
    for (source_type, connection_id), record_ids in grouped_ids.items():
        spec = _CITABLE_SOURCE_SPECS[source_type]
        try:
            rows_by_group[(source_type, connection_id)] = seadb_service.get_records_by_pks(
                base_id=base_id,
                table_name=_table_name(spec['table'], connection_id),
                pks=list(record_ids),
                fields_str=spec['fields'],
            ) or {}
        except Exception as e:
            logger.warning(
                'Failed to load portal citation records for %s connection %s: %s',
                source_type, connection_id, e,
            )
            rows_by_group[(source_type, connection_id)] = {}
    return rows_by_group


def _map_citation_records_to_sources(sources, rows_by_group):
    """Match loaded rows to their original source positions, including duplicates."""
    if not rows_by_group:
        return {}

    records_by_source = {}
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            continue
        connection_id = _connection_id(source)
        record_id = _record_id(source)
        rows = rows_by_group.get((source.get('type'), connection_id)) or {}
        record = _lookup_row(rows, record_id)
        if isinstance(record, dict):
            records_by_source[index] = record
    return records_by_source


def _table_name(table_attr, connection_id):
    from seaqa_ai.seadb_models.models import SchemaTables
    return getattr(SchemaTables, table_attr).table_name(connection_id)


def _project_uuid_32(project_uuid):
    project_uuid = str(project_uuid)
    if len(project_uuid) == 36:
        return uuid.UUID(project_uuid).hex
    return project_uuid


def _load_connection_configs(db_session_class, project_uuid, connection_ids):
    if not connection_ids or db_session_class is None or not project_uuid:
        return {}
    from seaqa_ai.db.models import ProjectConnection
    try:
        with db_session_class() as db_session:
            rows = db_session.query(ProjectConnection.id, ProjectConnection.config).filter(
                ProjectConnection.project_uuid == _project_uuid_32(project_uuid),
                ProjectConnection.id.in_(list(connection_ids)),
            ).all()
    except Exception as e:
        logger.warning(f'Failed to load portal citation connection configs: {e}')
        return {}

    configs = {}
    for connection_id, raw_config in rows:
        configs[connection_id] = _parse_config(raw_config)
    return configs


def _parse_config(raw_config):
    if isinstance(raw_config, dict):
        return raw_config
    if not isinstance(raw_config, str) or not raw_config.strip():
        return {}
    try:
        parsed = json.loads(raw_config)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _connection_id(source):
    try:
        return int(source.get('connection_id'))
    except (TypeError, ValueError):
        return None


def _lookup_row(rows, record_id):
    if not isinstance(rows, dict) or record_id is None:
        return None
    if record_id in rows:
        return rows[record_id]
    text_id = str(record_id)
    if text_id in rows:
        return rows[text_id]
    try:
        numeric_id = int(record_id)
    except (TypeError, ValueError):
        return None
    return rows.get(numeric_id)


def _record_id(source):
    record_id = source.get('_id')
    if record_id in (None, ''):
        return None
    try:
        return int(record_id)
    except (TypeError, ValueError):
        return record_id


def _http_url(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if value.startswith('http://') or value.startswith('https://'):
        return value
    return ''
