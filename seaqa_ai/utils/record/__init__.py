"""Shared record operations for Chat and Agent."""
from seaqa_ai.utils.record.retriever import RecordRetriever


def read_record(source_type, connection_id, record_id, project_uuid, app,
                loaded_records, context=None):
    """Internal preload, not a model observation and not a tool invocation."""
    return RecordRetriever(
        app, project_uuid, context=context, loaded_records=loaded_records,
    ).retrieve([{
        'source_type': source_type, 'connection_id': connection_id, 'record_id': record_id,
    }])[0]
