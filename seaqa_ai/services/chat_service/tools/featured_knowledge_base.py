from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.utils.constants import ExtraSourceType
from seaqa_ai.utils.record import ensure_record_context
from seaqa_ai.utils.tools import BasicTool


FEATURED_KNOWLEDGE_BASE_LIMIT = 20
FEATURED_KNOWLEDGE_BASE_SUMMARY_MAX_CHARS = 800


class GetFeaturedKnowledgeBase(BasicTool):
    tool = {
        'type': 'function',
        'name': 'get_featured_knowledge_base',
        'description': 'Get featured knowledge-base record metadata for the current project.',
        'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
        'strict': True,
    }

    def execute(self, context, app):
        source_type = ExtraSourceType.KNOWLEDGE_BASE.value
        allowed_sources = context.get('extra_sources')
        if allowed_sources is None:
            if context.get('is_external_portal'):
                return []
        elif source_type not in allowed_sources:
            return []

        table_name = SchemaTables.KNOWLEDGE_BASE.table_name()
        rows = app.seadb_service.query_rows(
            uuid_str_to_32_chars(context['project_uuid']),
            f'SELECT `_pk`, `title`, `ai_summary`, `tags`, `modified_time` FROM `{table_name}` '
            'WHERE `featured` = True AND (`deleted` = False OR `deleted` IS NULL) '
            'ORDER BY `modified_time` DESC, `created_time` DESC, `_pk` ASC '
            f'LIMIT {FEATURED_KNOWLEDGE_BASE_LIMIT}',
            convert_keys=False,
        ).get('results', [])
        if not rows:
            return []

        ensure_record_context(context)
        reference_pool = context['reference_pool']
        featured = []
        for row in rows:
            record_id = row.get('_pk')
            if type(record_id) is not int or record_id < 0:
                continue
            identity = {'source_type': source_type, 'connection_id': None, 'record_id': record_id}
            metadata = {
                'title': row.get('title') or '',
                'ai_summary': (row.get('ai_summary') or '')[:FEATURED_KNOWLEDGE_BASE_SUMMARY_MAX_CHARS],
                'tags': row.get('tags') or [],
            }
            reference_label = reference_pool.register_featured_record(
                identity, {**metadata, 'modified_time': row.get('modified_time')},
            )
            item = {**identity, **metadata}
            if reference_label is not None:
                item['reference_label'] = reference_label
            featured.append(item)
        return featured
