"""Rebuild an AgentTask and phase context from a persisted agent run."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from seaqa_ai.agent.agent_service.phase_promoters import AgentPhasePromoter, _RecordInputFormatter
from seaqa_ai.agent.models import AgentEvent, AgentTask
from seaqa_ai.agent.tasks_manager.constants import AGENT_TASK_SCHEMA_VERSION
from seaqa_ai.utils.record import read_record
from seaqa_ai.utils.record.loaded_records import LoadedRecords
from seaqa_ai.utils.record.identity import identity_key
from seaqa_ai.db.models import ProjectConnection
from seaqa_ai.utils import parse_datetime, parse_json, uuid_str_to_32_chars
from seaqa_ai.utils.constants import ConnectionType
from seaqa_ai.utils.llm_api import get_responses_client_by_model_id
from seaqa_ai.agent.agent_service.constants import LINKED_TICKET_TRACE_EVENT_TYPES


logger = logging.getLogger(__name__)


def regenerate_agent_suggestions(
    app,
    *,
    project_uuid,
    run,
    phase_actions,
    user_instruction,
    previous_drafts=None,
):
    """Rebuild context from a persisted run and generate suggestion drafts."""
    from seaqa_ai.agent.agent_service import AgentService

    project = app.seaqa_db.get_project_by_uuid(project_uuid)
    if not project:
        raise ValueError('Project not found.')
    project = _build_agent_project_context(app, project)

    task = rebuild_agent_task(project_uuid, run)
    agent_service = AgentService(app, project)
    context = agent_service._build_context(task)
    loaded_records = LoadedRecords()
    context['loaded_records'] = loaded_records
    context['sources_results'] = []

    trigger_record = read_record(
        source_type=task.trigger_event.source_type,
        connection_id=task.trigger_event.connection_id,
        record_id=task.trigger_event.record_id,
        project_uuid=task.project_uuid,
        app=app,
        loaded_records=loaded_records,
        context=context,
    )
    tracer, trace_context = agent_service._build_trace_context(
        task, context, loaded_records, trigger_record,
    )
    context.update(trace_context)
    _ensure_linked_ticket_from_owner(app, task, run, context, loaded_records)
    context['run_id'] = run.get('_pk') or run.get('id')

    promoter = AgentPhasePromoter(
        app,
        task,
        get_responses_client_by_model_id(
            app.data_logger,
            ((project.get('settings') or {}).get('agent') or {}).get('model'),
        ),
        tracer,
        context,
        loaded_records,
        project_prompt=(project.get('settings') or {}).get('prompt', ''),
    )
    phase_outputs = restore_phase_outputs(promoter, phase_actions)
    promoter.result.update(phase_outputs)
    promoter.start_regeneration(
        user_instruction,
        previous_drafts,
        error_message=run.get('error_message') or None,
        resume_from=_resume_from(phase_outputs),
    )
    return {
        'suggestions': [
            _serialize_draft_suggestion(row)
            for row in tracer
            if row.get('action_type') == 'suggestion'
        ],
    }


def rebuild_agent_task(project_uuid, run) -> AgentTask:
    event = _parse_run_event(run.get('event'))
    trigger_source = event.get('trigger_source')
    if not isinstance(trigger_source, dict):
        raise ValueError('Run event is missing trigger_source.')

    source_type = str(trigger_source.get('type') or '').strip()
    if not source_type:
        raise ValueError('trigger_source.type is required.')
    connection_id, record_id = _parse_source_id(trigger_source.get('id'))
    event_type = event.get('type')
    if not event_type:
        raise ValueError('Run event is missing type.')

    received_at = parse_datetime(run.get('started_at')) or datetime.now(timezone.utc)
    agent_event = AgentEvent(
        project_uuid=project_uuid,
        source_type=source_type,
        record_id=record_id,
        event_type=event_type,
        event=event,
        connection_id=connection_id,
        modified_time=None,
        received_at=received_at,
    )
    return AgentTask(
        schema_version=AGENT_TASK_SCHEMA_VERSION,
        task_id=str(uuid4()),
        project_uuid=project_uuid,
        trigger_event=agent_event,
        created_at=datetime.now(timezone.utc),
    )


def restore_phase_outputs(promoter, phase_actions) -> dict:
    outputs = {}
    completed = _completed_phase_outputs(phase_actions)

    prelude_output = completed.get('prelude')
    if prelude_output is not None:
        trigger_identity = _RecordInputFormatter.trigger_record_identity(promoter.task)
        linked_ticket_identity = promoter.context.get('linked_ticket_identity')
        selected_records = prelude_output.get('event_related_records', [])
        _reload_selected_records(promoter, selected_records)
        prelude_output['event_related_records'] = (
            _RecordInputFormatter.build_event_related_records(
                trigger_identity,
                linked_ticket_identity,
                selected_records,
                promoter.context,
                promoter.loaded_records,
            )
        )
        outputs['prelude'] = prelude_output

    analysis_output = completed.get('analysis')
    if analysis_output is not None:
        analysis_output['reference_related_records'] = (
            _RecordInputFormatter.build_reference_related_records(
                analysis_output.get('sources', []),
            )
        )
        outputs['analysis'] = analysis_output

    return outputs


def _reload_selected_records(promoter, records):
    """Reload persisted Prelude selections; filtering accepts only loaded records."""
    if not isinstance(records, list):
        return
    for record in records:
        record_key = identity_key(record)
        if record_key is None or promoter.loaded_records.contains(record):
            continue
        source_type, connection_id, record_id = record_key
        try:
            read_record(
                source_type=source_type,
                connection_id=connection_id,
                record_id=record_id,
                project_uuid=promoter.task.project_uuid,
                app=promoter.app,
                loaded_records=promoter.loaded_records,
                context=promoter.context,
            )
        except Exception:
            logger.warning(
                'Failed to reload record %s:%s:%s while rebuilding run context.',
                source_type,
                connection_id,
                record_id,
                exc_info=True,
            )


def _resume_from(phase_outputs) -> str:
    if 'analysis' in phase_outputs:
        return 'regeneration'
    if 'prelude' in phase_outputs:
        return 'analysis'
    return 'prelude'


def _completed_phase_outputs(phase_actions) -> dict:
    outputs = {}
    if not isinstance(phase_actions, list):
        return outputs
    for action in phase_actions:
        if not isinstance(action, dict):
            continue
        action_type = action.get('action_type') or action.get('type')
        if action_type not in ('prelude', 'analysis'):
            continue
        if str(action.get('status') or '').strip() and action.get('status') != 'completed':
            continue
        parsed = parse_json(action.get('result'))
        if isinstance(parsed, dict):
            outputs[action_type] = parsed
    return outputs


def _parse_run_event(raw_event) -> dict:
    parsed = parse_json(raw_event)
    if not isinstance(parsed, dict):
        raise ValueError('Run event is invalid.')
    return parsed


def _parse_source_id(source_id):
    source_id = str(source_id or '').strip()
    if not source_id:
        raise ValueError('trigger_source.id is required.')
    if '_' in source_id:
        connection_id, record_id = source_id.split('_', 1)
        return int(connection_id), int(record_id)
    return None, int(source_id)


def _ensure_linked_ticket_from_owner(app, task, run, context, loaded_records):
    if task.trigger_event.event_type not in LINKED_TICKET_TRACE_EVENT_TYPES:
        return
    if context.get('linked_ticket_identity'):
        return
    if run.get('owner_source_type') != 'ticket':
        return
    try:
        ticket_id = int(run.get('owner_source_id'))
    except (TypeError, ValueError):
        return
    try:
        linked_ticket_record = read_record(
            source_type='ticket',
            connection_id=None,
            record_id=ticket_id,
            project_uuid=task.project_uuid,
            app=app,
            loaded_records=loaded_records,
            context=context,
        )
    except Exception:
        logger.warning(
            'Failed to load owner ticket %s while rebuilding run context.',
            ticket_id,
            exc_info=True,
        )
        return
    context['linked_ticket_record'] = linked_ticket_record
    context['linked_ticket_identity'] = {
        'source_type': 'ticket',
        'connection_id': None,
        'record_id': int(linked_ticket_record.get('_pk') or ticket_id),
    }
    context['source_type'] = 'ticket'
    context['source_id'] = ticket_id
    context['source_title'] = linked_ticket_record.get('title', '') or run.get('owner_source_title', '')


def _build_agent_project_context(app, project):
    project = dict(project)
    project_uuid = uuid_str_to_32_chars(project['uuid'])
    connection_types = {
        'document_connections': ConnectionType.get_document_connection_types(),
        'issue_connections': ConnectionType.get_issue_connection_types(),
        'task_connections': ConnectionType.get_task_connection_types(),
    }
    with app.db_session_class() as db_session:
        for context_key, types in connection_types.items():
            project[context_key] = (
                ProjectConnection.get_connections_by_project_and_types(
                    db_session, project_uuid, types,
                )
                if types else []
            )
    return project


def _serialize_draft_suggestion(row):
    payload = row.get('suggestion_payload')
    parsed_payload = parse_json(payload)
    if not isinstance(parsed_payload, dict):
        parsed_payload = {}
    return {
        'tool_name': row.get('tool_name') or '',
        'status': row.get('status') or 'pending',
        'suggestion_reason': row.get('suggestion_reason') or '',
        'suggestion_content': row.get('suggestion_content') or '',
        'suggestion_payload': parsed_payload,
        'target_item_type': row.get('target_item_type') or '',
        'target_item_id': row.get('target_item_id') or '',
        'target_item_title': row.get('target_item_title') or '',
        'action_type': 'suggestion',
        'phase': row.get('phase') or 'regeneration',
    }
