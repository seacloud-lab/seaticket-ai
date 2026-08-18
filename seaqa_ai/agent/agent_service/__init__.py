"""Deterministic multi-phase workflow for one typed agent task."""
import json
import logging
import threading

from seaqa_ai.agent.models import AgentTask
from seaqa_ai.agent.agent_service.constants import (
    LINKED_TICKET_TRACE_EVENT_TYPES,
    REQUIRED_LINKED_TICKET_TRACE_EVENT_TYPES,
)
from seaqa_ai.agent.agent_service.phase_promoters import AgentPhasePromoter
from seaqa_ai.agent.tools.get_record import GetRecord
from seaqa_ai.agent.utils.react.tracer import AgentReACTTracer
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.utils import now_iso
from seaqa_ai.utils.llm_api import get_responses_client_by_model_id


logger = logging.getLogger(__name__)


class MissingRequiredLinkedTicket(Exception):
    """A trigger that requires a linked Ticket has none."""


class AgentService:
    """Execute one task through the deterministic phase workflow."""

    def __init__(self, app, project):
        self.app = app
        self.project = project

    ##### Agent running #####
    def run(self, task: AgentTask) -> dict:
        if not isinstance(task, AgentTask):
            raise TypeError('task must be an AgentTask')

        run_id = None
        tracer = None
        traces_flushed = False
        try:
            context = self._build_context(task)

            record_cache = {}
            context['record_cache'] = record_cache

            target_record = GetRecord().execute(
                source_type=task.target_source_type,
                connection_id=task.target_connection_id,
                record_id=task.target_record_id,
                project_uuid=task.project_uuid,
                app=self.app,
                record_cache=record_cache,
                context=context,
            )

            tracer, trace_context = self._build_trace_context(
                task, context, record_cache, target_record,
            )
            context.update(trace_context)

            run_id = self._create_run_record(task)
            context['run_id'] = run_id

            responses_client = get_responses_client_by_model_id(
                self.app.data_logger,
                ((self.project.get('settings') or {}).get('agent') or {}).get('model'),
            )

            AgentPhasePromoter(
                self.app,
                task,
                responses_client,
                tracer,
                context,
                record_cache,
                project_prompt=(self.project.get('settings') or {}).get('prompt', ''),
            ).start()

            self._finalize_run_record(
                task, run_id, status='completed', items_processed=1,
            )
            pending_suggestion_action_ids = tracer.flush()
            traces_flushed = True
            threading.Thread(
                target=self._trigger_auto_actions,
                args=(task.project_uuid, pending_suggestion_action_ids),
                name='agent_auto_actions',
                daemon=True,
            ).start()

        except MissingRequiredLinkedTicket:
            logger.warning(
                'Skipping agent task without required linked ticket: '
                'task_id=%s target=%s:%s event_type=%s',
                task.task_id,
                task.target_source_type,
                task.target_record_id,
                task.trigger_event.event_type,
            )
        except Exception as error:
            if run_id is not None:
                self._finalize_run_record(
                    task,
                    run_id,
                    status='failed',
                    items_processed=0,
                    error_message=str(error)[:1000],
                )
            logger.exception(
                'Agent task failed: task_id=%s project=%s target=%s:%s',
                task.task_id,
                task.project_uuid,
                task.target_source_type,
                task.target_record_id,
            )
            raise
        finally:
            if tracer is not None and not traces_flushed:
                tracer.flush()

    def _trigger_auto_actions(self, project_uuid, action_ids):
        if not action_ids or self.app.seaqa_web_api is None:
            return
        try:
            self.app.seaqa_web_api.execute_auto_actions(
                project_uuid=project_uuid,
                action_ids=action_ids,
            )
        except Exception:
            logger.warning(
                'Failed to trigger auto actions %s for project %s. '
                'Actions remain pending for manual confirmation.',
                action_ids,
                project_uuid,
                exc_info=True,
            )

    ##### Agent context #####
    def _build_context(self, task):
        return {
            'project_uuid': task.project_uuid,
            'task_id': task.task_id,
            'triggering_event': task.trigger_event.event,
            'document_connections': self.project.get('document_connections', []),
            'issue_connections': self.project.get('issue_connections', []),
            'task_connections': self.project.get('task_connections', []),
        }

    @staticmethod
    def _source_id(connection_id, record_id):
        return record_id if connection_id is None else f'{connection_id}_{record_id}'

    def _build_trace_context(self, task, context, record_cache, target_record):
        if task.trigger_event.event_type in LINKED_TICKET_TRACE_EVENT_TYPES:
            linked_ticket_id = target_record.get('linked_ticket')
            if linked_ticket_id:
                try:
                    linked_ticket_record = GetRecord().execute(
                        source_type='ticket',
                        connection_id=None,
                        record_id=linked_ticket_id,
                        project_uuid=task.project_uuid,
                        app=self.app,
                        record_cache=record_cache,
                        context=context,
                    )
                except Exception:
                    logger.warning(
                        'Failed to load linked ticket %s for %s:%s; using the triggering record.',
                        linked_ticket_id,
                        task.target_source_type,
                        task.target_record_id,
                        exc_info=True,
                    )
                else:
                    return self._build_linked_ticket_trace_context(
                        task, target_record, linked_ticket_record,
                    )
            elif task.trigger_event.event_type in REQUIRED_LINKED_TICKET_TRACE_EVENT_TYPES:
                raise MissingRequiredLinkedTicket
        return self._build_target_trace_context(task, target_record)

    def _build_target_trace_context(self, task, target_record):
        source_id = self._source_id(task.target_connection_id, task.target_record_id)
        source_title = target_record.get('title', '')
        return self._new_tracer(task, task.target_source_type, source_id, source_title), {
            'source_type': task.target_source_type,
            'source_id': source_id,
            'source_title': source_title,
            'trigger_source_type': task.target_source_type,
            'trigger_source_id': source_id,
            'trigger_source_title': source_title,
            'target_record': target_record,
        }

    def _build_linked_ticket_trace_context(self, task, target_record, linked_ticket_record):
        trigger_source_id = self._source_id(task.target_connection_id, task.target_record_id)
        trigger_source_title = target_record.get('title', '')
        linked_ticket_id = int(linked_ticket_record['_pk'])
        linked_ticket_identity = {
            'source_type': 'ticket',
            'connection_id': None,
            'record_id': linked_ticket_id,
        }
        source_title = linked_ticket_record.get('title', '')
        return self._new_tracer(task, 'ticket', linked_ticket_id, source_title), {
            'source_type': 'ticket',
            'source_id': linked_ticket_id,
            'source_title': source_title,
            'trigger_source_type': task.target_source_type,
            'trigger_source_id': trigger_source_id,
            'trigger_source_title': trigger_source_title,
            'target_record': target_record,
            'linked_ticket_record': linked_ticket_record,
            'linked_ticket_identity': linked_ticket_identity,
        }

    ##### Tracer & SeaDB record OP #####
    def _new_tracer(self, task, source_type, source_id, source_title):
        return AgentReACTTracer(
            task.project_uuid,
            self.app.seadb_service,
            target_source_type=source_type,
            target_source_id=source_id,
            target_source_title=source_title,
        )

    def _create_run_record(self, task):
        response = self.app.seadb_service.insert_rows(
            task.project_uuid,
            SchemaTables.AGENT_RUNS.table_name(),
            [{
                'status': 'running',
                'started_at': now_iso(),
                'items_processed': 0,
                'events': json.dumps(task.trigger_event.event, ensure_ascii=False),
            }],
        )
        pks = response.get('pks', [])
        if len(pks) != 1:
            raise RuntimeError(f'Expected one agent run record, received {len(pks)}')
        return pks[0]

    def _finalize_run_record(self, task, run_id, *, status, items_processed, error_message=''):
        self.app.seadb_service.update_rows(
            task.project_uuid,
            SchemaTables.AGENT_RUNS.table_name(),
            [{'pk': run_id, 'row': {
                'status': status,
                'finished_at': now_iso(),
                'items_processed': items_processed,
                'error_message': error_message,
            }}],
        )
        if status == 'completed' and task.target_source_type == 'ticket':
            self.app.seadb_service.update_rows(
                task.project_uuid,
                SchemaTables.TICKETS.table_name(),
                [{'pk': task.target_record_id, 'row': {
                    'last_agent_processed_at': now_iso(),
                }}],
            )
