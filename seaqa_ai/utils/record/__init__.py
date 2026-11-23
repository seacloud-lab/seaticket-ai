"""Shared record operations for Chat and Agent."""
from seaqa_ai.utils.record.loaded_records import LoadedRecords
from seaqa_ai.utils.record.observation_tracker import RecordObservationTracker
from seaqa_ai.utils.record.reference_pool import ReferencePool
from seaqa_ai.utils.record.retriever import RecordRetriever


def ensure_record_context(context: dict) -> dict:
    """Initialize shared per-request/per-run record state on ``context`` if absent."""
    if context.get('loaded_records') is None:
        context['loaded_records'] = LoadedRecords()
    if context.get('record_observations') is None:
        context['record_observations'] = RecordObservationTracker()
    if context.get('reference_pool') is None:
        context['reference_pool'] = ReferencePool()
    return context


def read_record(source_type, connection_id, record_id, project_uuid, app, context=None):
    """Internal preload, not a model observation and not a tool invocation."""
    return RecordRetriever(
        app, project_uuid, context=context,
    ).retrieve([{
        'source_type': source_type, 'connection_id': connection_id, 'record_id': record_id,
    }])[0]
