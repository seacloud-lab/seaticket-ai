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
            'Read relevant documents listed in featured_document_catalog. The entries parameter is a '
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
        record_ids_white_list = context.get('_featured_record_ids_white_list', {})
        retrieval_metadata = context.get('_featured_retrieval_metadata', {})
        loaded_sources = tool_executor.cache.get('sources_results', [])
        loaded_source_labels = {self.record_hash(source): f'<reference_{i}>' for i, source in enumerate(loaded_sources)}
        remaining_source_budget = min(self.SINGLE_TOOL_MAX_SOURCES_COUNT, self.MAX_SOURCES_COUNT - len(loaded_sources))
        seen_entry_keys = set()
        requested_entry_keys = []
        entry_errors_and_notices = {}
        to_retrieve = {}
        type_map = {}
        for raw_connection_id, record_ids in entries.items():
            connection_id = int(raw_connection_id) if raw_connection_id.isdigit() else raw_connection_id
            for record_id in record_ids:
                entry_key = (connection_id, record_id)
                if entry_key in seen_entry_keys:
                    continue
                seen_entry_keys.add(entry_key)
                requested_entry_keys.append(entry_key)
                entry = {'connection_id': connection_id, 'record_id': record_id}
                if record_id not in record_ids_white_list.get(connection_id, []):
                    entry_errors_and_notices[entry_key] = {
                        'error': 'Document is not in the featured document white list.', **entry,
                    }
                    logger.warning('Invalid featured document entry: %s', entry)
                    continue
                source_type = retrieval_metadata.get(connection_id, {}).get(record_id, {}).get('type', connection_id)
                record_hash = gen_record_hash(source_type, connection_id, record_id)
                if record_hash in loaded_source_labels:
                    entry_errors_and_notices[entry_key] = {
                        'notice': 'Document was already provided.', **entry,
                        'reference_id': loaded_source_labels[record_hash],
                    }
                    continue
                if remaining_source_budget <= 0:
                    entry_errors_and_notices[entry_key] = {'notice': 'Document source limit reached.', **entry}
                    continue
                remaining_source_budget -= 1
                record = deepcopy(retrieval_metadata.get(connection_id, {}).get(record_id, {}))
                to_retrieve.setdefault(connection_id, {})[record_id] = record
                type_map[connection_id] = source_type

        if to_retrieve:
            from seaqa_ai.services.ai_services.utils.search.content_retriever import ContentRetriever

            retriever = ContentRetriever(app.username_resolver, app.seadb_service, uuid_str_to_32_chars(context['project_uuid']))
            retriever.retrieve(to_retrieve, type_map)
        retrieved_records = [record for group in to_retrieve.values() for record in group.values()]
        observations = self._provide_results(retrieved_records, context, app, tool_executor)
        results_by_key = {
            (record['connection_id'], record['_id']): observation
            for record, observation in zip(retrieved_records, observations)
        }
        results = []
        for entry_key in requested_entry_keys:
            if entry_key in entry_errors_and_notices:
                results.append(entry_errors_and_notices[entry_key])
                continue
            connection_id, record_id = entry_key
            results.append(results_by_key.get(entry_key) or {
                'error': 'Document no longer exists.',
                'connection_id': connection_id, 'record_id': record_id,
            })
        return results
