import logging
from copy import deepcopy

from seaqa_ai.utils import uuid_str_to_32_chars, gen_record_hash
from seaqa_ai.utils.tools.search_tools import BasicSearchTool, _SEARCH_TOOLS_RESULT_FORMAT

logger = logging.getLogger(__name__)


class ReadDocuments(BasicSearchTool):
    tool = {
        'type': 'function',
        'name': 'read_documents',
        'description': (
            'Read relevant documents listed in featured_documents. The entries parameter is a '
            'mapping from connection_id to record ID arrays, e.g. {"entries": {"knowledge_base": [1, 2]}}. '
            'Only IDs in the featured catalog are allowed. Results contain documents in this format: '
            f'{_SEARCH_TOOLS_RESULT_FORMAT} '
            'Invalid entries return error and entry information. Already provided documents return '
            'notice and their existing reference_id label; use the content already provided.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'entries': {
                    'type': 'object',
                    'additionalProperties': {
                        'type': 'array',
                        'items': {'type': 'integer'},
                    },
                    'minProperties': 1,
                },
            },
            'required': ['entries'],
            'additionalProperties': False,
        },
        'strict': False,
    }

    def execute(self, entries: dict, context, app, tool_executor):
        indexes = context.get('_featured_document_indexes', {})
        metadata = context.get('_featured_document_records', {})
        cached = tool_executor.cache.get('sources_results', [])
        cached_labels = {self.record_hash(record): f'<reference_{i}>' for i, record in enumerate(cached)}
        remaining = min(self.SINGLE_TOOL_MAX_SOURCES_COUNT, self.MAX_SOURCES_COUNT - len(cached))
        seen = set()
        requests = []
        to_retrieve = {}
        type_map = {}
        for raw_connection_id, record_ids in entries.items():
            connection_id = int(raw_connection_id) if raw_connection_id.isdigit() else raw_connection_id
            for record_id in record_ids:
                key = (connection_id, record_id)
                if key in seen:
                    continue
                seen.add(key)
                entry = {'connection_id': connection_id, 'record_id': record_id}
                if record_id not in indexes.get(connection_id, []):
                    requests.append({'error': 'Document is not in the featured document index.', **entry})
                    logger.warning('Invalid featured document entry: %s', entry)
                    continue
                source_type = metadata.get(connection_id, {}).get(record_id, {}).get('type', connection_id)
                record_hash = gen_record_hash(source_type, connection_id, record_id)
                if record_hash in cached_labels:
                    requests.append({
                        'notice': 'Document was already provided.', **entry,
                        'reference_id': cached_labels[record_hash],
                    })
                    continue
                if remaining <= 0:
                    requests.append({'notice': 'Document source limit reached.', **entry})
                    continue
                remaining -= 1
                record = deepcopy(metadata.get(connection_id, {}).get(record_id, {}))
                to_retrieve.setdefault(connection_id, {})[record_id] = record
                type_map[connection_id] = source_type
                requests.append(key)

        if to_retrieve:
            from seaqa_ai.services.ai_services.utils.search.content_retriever import ContentRetriever

            retriever = ContentRetriever(app.username_resolver, app.seadb_service, uuid_str_to_32_chars(context['project_uuid']))
            retriever.retrieve(to_retrieve, type_map)
        records = [record for group in to_retrieve.values() for record in group.values()]
        observations = self._provide_results(records, context, app, tool_executor)
        results_by_key = {
            (record['connection_id'], record['_id']): observation
            for record, observation in zip(records, observations)
        }
        results = []
        for request in requests:
            if isinstance(request, dict):
                results.append(request)
            else:
                results.append(results_by_key.get(request) or {
                    'error': 'Document no longer exists.',
                    'connection_id': request[0], 'record_id': request[1],
                })
        return results
