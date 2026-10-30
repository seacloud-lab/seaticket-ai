"""Bounded, deduplicated record observations shared by Chat and Agent."""
from collections import OrderedDict

from jsonschema import Draft202012Validator

from seaqa_ai import config
from seaqa_ai.utils.record.loaded_records import LoadedRecords
from seaqa_ai.utils import (
    gen_record_hash, MARKDOWN_IMAGE_RE, HTML_IMAGE_RE,
    extract_markdown_image_url, normalize_image_url,
)
from seaqa_ai.utils.constants import ExtraSourceType
from seaqa_ai.utils.record.identity import record_identity_schema
from seaqa_ai.utils.record.retriever import RecordRetriever
from seaqa_ai.utils.tools import BasicTool


class GetRecords(BasicTool):
    tool = {
        'type': 'function',
        'name': 'get_records',
        'description': (
            'Read current records by their identities without following linked records. '
            'Use targets to batch related reads. At most 10 distinct records are provided '
            'per request by default. Already provided records return a notice and reference_id. '
            'Each target returns its record, notice, or error independently. '
            'Order targets from most important to least important: remaining record slots '
            'are allocated in input order. Successful records include related_records identities; '
            'read those in a later step only if they can resolve a remaining evidence gap.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'targets': {
                    'type': 'array', 'minItems': 1,
                    'description': (
                        'Record identities ordered from MOST important to LEAST important. '
                        'Limited remaining slots go to earlier targets first. Include only '
                        'records needed to resolve the current information gap.'
                    ),
                    'items': record_identity_schema(nested=True),
                },
            },
            'required': ['targets'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, targets, context, app, tool_executor):
        if not isinstance(targets, list) or not targets:
            raise ValueError('targets must be a non-empty array')
        cache = tool_executor.cache
        if 'loaded_records' not in cache:
            cache['loaded_records'] = LoadedRecords()
        loaded = cache['loaded_records']
        scope = cache.setdefault('record_observation_scope', 'chat')
        retriever = RecordRetriever(
            app, context['project_uuid'].replace('-', ''), context=context, loaded_records=loaded,
        )
        validator = Draft202012Validator(record_identity_schema(nested=True))
        results = [None] * len(targets)
        pending = []
        for index, target in enumerate(targets):
            entry = {
                field: target.get(field) if isinstance(target, dict) else None
                for field in ('source_type', 'connection_id', 'record_id')
            }
            try:
                error = next(validator.iter_errors(target), None)
                if error:
                    raise ValueError(error.message)
                identity = retriever._normalize_target(target)
            except Exception as error:
                results[index] = {'error': str(error), **entry}
                continue
            pending.append((index, identity))

        # Reserve slots in global priority order BEFORE grouping database reads.
        # Failed reads release slots for the next candidates, again in input order.
        while pending:
            capacity = max(0, config.GET_RECORDS_MAX_RECORDS - loaded.reported_count)
            batch = OrderedDict()
            deferred = []
            for index, identity in pending:
                reference_id = loaded.reference_id(identity, scope)
                key = loaded.key(**identity)
                if reference_id is not None:
                    results[index] = {
                        'notice': 'Document was already provided.', **identity,
                        'reference_id': reference_id,
                    }
                elif key in batch:
                    batch[key]['entries'].append((index, identity))
                elif loaded.was_reported(identity) or capacity > 0:
                    batch[key] = {'identity': identity, 'entries': [(index, identity)]}
                    if not loaded.was_reported(identity):
                        capacity -= 1
                else:
                    deferred.append((index, identity))
            if not batch:
                for index, identity in deferred:
                    results[index] = {'notice': 'Document source limit reached.', **identity}
                break
            groups = OrderedDict()
            outcomes = {}
            for item in batch.values():
                identity = item['identity']
                cached_record = loaded.get(**identity)
                if cached_record is not None:
                    outcomes[loaded.key(**identity)] = (cached_record, None)
                    continue
                groups.setdefault((identity['source_type'], identity['connection_id']), []).append(item)
            for items in groups.values():
                try:
                    records = retriever.retrieve([item['identity'] for item in items], allow_missing=True)
                except Exception as error:
                    for item in items:
                        outcomes[loaded.key(**item['identity'])] = (None, str(error))
                else:
                    for item, record in zip(items, records):
                        outcomes[loaded.key(**item['identity'])] = (record, None)
            for key, item in batch.items():
                record, error = outcomes[key]
                for offset, (index, identity) in enumerate(item['entries']):
                    if error or record is None:
                        results[index] = {'error': error or 'No active record found.', **identity}
                    elif offset:
                        results[index] = {
                            'notice': 'Document was already provided.', **identity,
                            'reference_id': loaded.reference_id(identity, scope),
                        }
                    else:
                        reference_id = self._register_source(cache, identity, record)
                        loaded.mark_reported(identity, scope, reference_id)
                        results[index] = {
                            **record, **identity, 'reference_id': reference_id,
                            'related_records': retriever.related_records(record),
                        }
            pending = deferred
        return results

    @staticmethod
    def _register_source(cache, identity, record):
        sources = cache.setdefault('sources_results', [])
        record_hash = gen_record_hash(**identity)
        images = GetRecords._image_urls(record.get('content'))
        for index, source in enumerate(sources):
            if gen_record_hash(source['type'], source.get('connection_id'), source['_id']) == record_hash:
                for key in ('url', 'modified_time'):
                    if record.get(key) is not None:
                        source[key] = record[key]
                if images:
                    source['images'] = images
                return f'<reference_{index}>'
        source = {
            key: value for key, value in record.items()
            if key in ('title', 'url', 'modified_time', 'state', 'resolved', 'linked_ticket')
        }
        source.update({
            'type': identity['source_type'], '_id': identity['record_id'],
            'connection_id': identity['source_type'] if ExtraSourceType.is_valid(identity['source_type']) else identity['connection_id'],
        })
        if images:
            source['images'] = images
        sources.append(source)
        return f'<reference_{len(sources) - 1}>'

    @staticmethod
    def _image_urls(value):
        if isinstance(value, dict):
            contents = value.values()
        elif isinstance(value, list):
            contents = value
        elif isinstance(value, str):
            urls = [extract_markdown_image_url(url) for url in MARKDOWN_IMAGE_RE.findall(value)]
            urls.extend(HTML_IMAGE_RE.findall(value))
            return list(dict.fromkeys(url for raw in urls if (url := normalize_image_url(raw))))
        else:
            return []
        return list(dict.fromkeys(url for content in contents for url in GetRecords._image_urls(content)))
