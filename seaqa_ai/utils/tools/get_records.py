"""Bounded, deduplicated record observations shared by Chat and Agent."""
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from seaqa_ai import config
from seaqa_ai.utils import (
    HTML_IMAGE_RE, MARKDOWN_IMAGE_RE,
    extract_markdown_image_url, normalize_image_url,
)
from seaqa_ai.utils.record import ensure_record_context
from seaqa_ai.utils.record.identity import record_identity_schema, record_key
from seaqa_ai.utils.record.loaded_records import LoadedRecords
from seaqa_ai.utils.record.observation_tracker import RecordObservationTracker
from seaqa_ai.utils.record.retriever import RecordRetriever
from seaqa_ai.utils.tools import BasicTool


_IDENTITY_FIELDS = ('source_type', 'connection_id', 'record_id')


@dataclass
class _ScheduledRead:
    """One distinct record scheduled to be read in the current reservation pass."""
    record_key: str
    identity: dict
    target_indices: list = field(default_factory=list)


class GetRecords(BasicTool):
    tool = {
        'type': 'function',
        'name': 'get_records',
        'description': (
            'Read current records by their identities without following linked records. '
            'Use targets to batch related reads. At most 10 distinct records are provided '
            'per request by default. Already provided records return a notice and reference_label. '
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

    DUPLICATE_NOTICE = 'Document was already provided.'
    SOURCE_LIMIT_NOTICE = 'Document source limit reached.'
    _TARGET_VALIDATOR = Draft202012Validator(record_identity_schema(nested=True))

    def execute(self, targets, context, app, *, register_source=True):
        if not isinstance(targets, list) or not targets:
            raise ValueError('targets must be a non-empty array')

        ensure_record_context(context)
        loaded_records = context['loaded_records']
        observations = context['record_observations']
        observation_scope = context.setdefault('record_observation_scope', 'chat')
        retriever = RecordRetriever(
            app, context['project_uuid'].replace('-', ''), context=context,
        )

        results = [None] * len(targets)
        remaining_targets = self._validate_targets(targets, retriever, results)

        # Reserve slots in global priority order BEFORE grouping database reads.
        # Failed reads do not mark records as reported, releasing their slots
        # to waitlisted_targets in the next pass.
        while remaining_targets:
            scheduled_reads, waitlisted_targets = self._schedule_next_pass(
                remaining_targets, observations, observation_scope, results,
            )
            if not scheduled_reads:
                # if there are no scheduled records, and there are waitlisted targets, return the source limit notice
                for index, identity in waitlisted_targets:
                    results[index] = {'notice': self.SOURCE_LIMIT_NOTICE, **identity}
                break

            fetched_by_key = self._fetch_scheduled_records(scheduled_reads, loaded_records, retriever)

            self._fill_pass_results(
                scheduled_reads,
                fetched_by_key,
                observations=observations,
                observation_scope=observation_scope,
                retriever=retriever,
                context=context,
                register_source=register_source,
                results=results,
            )
            remaining_targets = waitlisted_targets

        return results

    @classmethod
    def _validate_targets(cls, targets, retriever, results):
        valid_targets = []
        for index, target in enumerate(targets):
            raw_identity = {
                field: target.get(field) if isinstance(target, dict) else None
                for field in _IDENTITY_FIELDS
            }
            try:
                schema_error = next(cls._TARGET_VALIDATOR.iter_errors(target), None)
                if schema_error:
                    raise ValueError(schema_error.message)
                identity = retriever._normalize_target(target)
            except Exception as error:
                results[index] = {'error': str(error), **raw_identity}
                continue
            valid_targets.append((index, identity))
        return valid_targets

    @classmethod
    def _schedule_next_pass(
        cls,
        candidate_targets: list[tuple[int, dict]],
        observations: RecordObservationTracker,
        observation_scope: str,
        results: list[dict],
    ):
        remaining_slots = max(0, config.GET_RECORDS_MAX_RECORDS - observations.reported_count)
        scheduled_reads: dict[str, _ScheduledRead] = {} # record_key -> _ScheduledRead
        waitlisted_targets: list[tuple[int, dict]] = [] # (index, identity)

        for index, identity in candidate_targets:
            key = record_key(**identity)

            # if the record is already in the scope, return the duplicate notice
            if observations.in_scope(identity, observation_scope):
                reference_label = observations.reference_label(identity, observation_scope)
                results[index] = cls._duplicate_notice(identity, reference_label)
                continue

            # if the record is already scheduled, add the index to the target indices
            if key in scheduled_reads:
                scheduled_reads[key].target_indices.append(index)
                continue

            needs_new_slot = not observations.was_reported(identity)

            # if the record is not reported, and there are no remaining slots, add the target to the waitlisted targets
            if needs_new_slot and remaining_slots == 0:
                waitlisted_targets.append((index, identity))
                continue

            # if the record is not reported, and there are remaining slots, schedule the record
            if needs_new_slot:
                remaining_slots -= 1
            scheduled_reads[key] = _ScheduledRead(
                record_key=key,
                identity=identity,
                target_indices=[index],
            )

        return scheduled_reads, waitlisted_targets

    @staticmethod
    def _fetch_scheduled_records(scheduled_reads: dict[str, _ScheduledRead], loaded_records: LoadedRecords, retriever: RecordRetriever):
        fetched_by_key: dict[str, tuple[dict | None, str | None]] = {} # record_key -> (record, error_message)  
        reads_by_connection: dict[tuple[str, str], list[_ScheduledRead]] = {} # (source_type, connection_id) -> [_ScheduledRead]

        for read_task in scheduled_reads.values():
            identity = read_task.identity
            cached_record = loaded_records.get(**identity)
            if cached_record is not None:
                fetched_by_key[read_task.record_key] = (cached_record, None)
                continue
            source_group_key = (identity['source_type'], identity['connection_id'])
            reads_by_connection.setdefault(source_group_key, []).append(read_task)

        for connection_reads in reads_by_connection.values():
            identities = [read_task.identity for read_task in connection_reads]
            try:
                records = retriever.retrieve(identities, allow_missing=True)
            except Exception as error:
                error_message = str(error)
                for read_task in connection_reads:
                    fetched_by_key[read_task.record_key] = (None, error_message)
            else:
                for read_task, record in zip(connection_reads, records):
                    fetched_by_key[read_task.record_key] = (record, None)

        return fetched_by_key

    @classmethod
    def _fill_pass_results(
        cls, scheduled_reads: dict[str, _ScheduledRead], fetched_by_key: dict[str, tuple[dict | None, str | None]], *,
        observations: RecordObservationTracker, observation_scope: str, retriever: RecordRetriever, context: dict, register_source: bool, results: list[dict],
    ):
        for key, read_task in scheduled_reads.items():
            identity = read_task.identity
            record, error_message = fetched_by_key[key]

            # if there is an error, return the error notice
            if error_message or record is None:
                for index in read_task.target_indices:
                    results[index] = cls._error_notice(identity, error_message or 'No active record found.')
                continue

            primary_index, *duplicate_indices = read_task.target_indices
            reference_label = (
                cls._register_source(context, identity, record)
                if register_source else None
            )
            observations.mark_reported(identity, observation_scope, reference_label)

            primary_result = {**record, **identity}
            if reference_label is not None:
                primary_result['reference_label'] = reference_label
            primary_result['related_records'] = retriever.related_records(record)
            results[primary_index] = primary_result

            for duplicate_index in duplicate_indices:
                results[duplicate_index] = cls._duplicate_notice(identity, reference_label)

    @classmethod
    def _duplicate_notice(cls, identity, reference_label=None):
        notice = {'notice': cls.DUPLICATE_NOTICE, **identity}
        if reference_label is not None:
            notice['reference_label'] = reference_label
        return notice
    
    @classmethod
    def _error_notice(cls, identity: dict, error_message: str):
        return {
            'error': error_message,
            **identity,
        }

    @classmethod
    def _register_source(cls, context, identity, record):
        ensure_record_context(context)
        reference_pool = context['reference_pool']
        image_urls = cls._extract_image_urls(record.get('content'))
        return reference_pool.register_record(identity, record, image_urls=image_urls)

    @classmethod
    def _extract_image_urls(cls, content):
        if isinstance(content, dict):
            nested_items = content.values()
        elif isinstance(content, list):
            nested_items = content
        elif isinstance(content, str):
            raw_urls = [extract_markdown_image_url(url) for url in MARKDOWN_IMAGE_RE.findall(content)]
            raw_urls.extend(HTML_IMAGE_RE.findall(content))
            return list(dict.fromkeys(
                normalized for raw in raw_urls if (normalized := normalize_image_url(raw))
            ))
        else:
            return []
        return list(dict.fromkeys(
            url for item in nested_items for url in cls._extract_image_urls(item)
        ))
