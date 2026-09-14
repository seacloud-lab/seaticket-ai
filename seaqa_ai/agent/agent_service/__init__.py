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
from seaqa_ai.utils.constants import LANGUAGE
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
                source_type=task.target_item_type,
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

            run_id = self._create_run_record(
                task,
                owner_source_type=trace_context.get('source_type', ''),
                owner_source_id=trace_context.get('source_id', ''),
                owner_source_title=trace_context.get('source_title', ''),
                trigger_source_type=trace_context.get('trigger_source_type', ''),
                trigger_source_id=trace_context.get('trigger_source_id', ''),
                trigger_source_title=trace_context.get('trigger_source_title', ''),
            )
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

            flush_result = tracer.flush()
            if not flush_result.get('succeeded'):
                raise RuntimeError('Failed to persist agent actions for this run.')
            traces_flushed = True

            self._finalize_run_record(
                task,
                run_id,
                status='completed',
                suggestions_status=flush_result.get('suggestions_status', 'none'),
            )
            pending_suggestion_action_ids = flush_result.get('pending_suggestion_action_ids', [])
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
                task.target_item_type,
                task.target_record_id,
                task.trigger_event.event_type,
            )
        except Exception as error:
            if run_id is not None:
                self._finalize_run_record(
                    task,
                    run_id,
                    status='failed',
                    error_message=str(error)[:1000],
                    suggestions_status='failed',
                )
            logger.exception(
                'Agent task failed: task_id=%s project=%s target=%s:%s',
                task.task_id,
                task.project_uuid,
                task.target_item_type,
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
        settings = self.project.get('settings') or {}
        lang = settings.get('lang') or 'en'
        skills_settings = settings.get('skills') or {}
        if not isinstance(skills_settings, dict):
            skills_settings = {}
        disabled_builtin_skills = skills_settings.get('disabled_builtins') or []
        if not isinstance(disabled_builtin_skills, list):
            disabled_builtin_skills = []
        return {
            'project_uuid': task.project_uuid,
            'task_id': task.task_id,
            'target_language': LANGUAGE.get(lang, 'English'),
            'triggering_event': task.trigger_event.event,
            'document_connections': self.project.get('document_connections', []),
            'issue_connections': self.project.get('issue_connections', []),
            'task_connections': self.project.get('task_connections', []),
            'disabled_builtin_skills': disabled_builtin_skills,
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
                        task.target_item_type,
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
        return self._new_tracer(task), {
            'source_type': task.target_item_type,
            'source_id': source_id,
            'source_title': source_title,
            'trigger_source_type': task.target_item_type,
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
        return self._new_tracer(task), {
            'source_type': 'ticket',
            'source_id': linked_ticket_id,
            'source_title': source_title,
            'trigger_source_type': task.target_item_type,
            'trigger_source_id': trigger_source_id,
            'trigger_source_title': trigger_source_title,
            'target_record': target_record,
            'linked_ticket_record': linked_ticket_record,
            'linked_ticket_identity': linked_ticket_identity,
        }

    ##### Tracer & SeaDB record OP #####
    def _new_tracer(self, task):
        return AgentReACTTracer(
            task.project_uuid,
            self.app.seadb_service,
        )

    def _create_run_record(
        self,
        task,
        *,
        owner_source_type,
        owner_source_id,
        owner_source_title,
        trigger_source_type,
        trigger_source_id,
        trigger_source_title,
    ):
        event = dict(task.trigger_event.event)
        new_value = event.get('new_value')
        if isinstance(new_value, dict):
            event['new_value'] = {
                key: value for key, value in new_value.items() if key != 'content'
            }
        # The record that the triggering event fired on. When the run is owned by
        # a linked ticket this differs from owner_source_* and lets the UI jump
        # to the actual source record (e.g. the GitHub issue, not the ticket).
        event['trigger_source'] = {
            'type': trigger_source_type or '',
            'id': str(trigger_source_id) if trigger_source_id is not None else '',
            'title': trigger_source_title or '',
        }
        response = self.app.seadb_service.insert_rows(
            task.project_uuid,
            SchemaTables.AGENT_RUNS.table_name(),
            [{
                'status': 'running',
                'owner_source_type': owner_source_type,
                'owner_source_id': str(owner_source_id) if owner_source_id is not None else '',
                'owner_source_title': owner_source_title or '',
                'suggestions_status': '',
                'started_at': now_iso(),
                'event': json.dumps(event, ensure_ascii=False),
            }],
        )
        pks = response.get('pks', [])
        if len(pks) != 1:
            raise RuntimeError(f'Expected one agent run record, received {len(pks)}')
        return pks[0]

    def _finalize_run_record(
        self,
        task,
        run_id,
        *,
        status,
        error_message='',
        suggestions_status='',
    ):
        self.app.seadb_service.update_rows(
            task.project_uuid,
            SchemaTables.AGENT_RUNS.table_name(),
            [{'pk': run_id, 'row': {
                'status': status,
                'finished_at': now_iso(),
                'error_message': error_message,
                'suggestions_status': suggestions_status,
            }}],
        )
        if status == 'completed' and task.target_item_type == 'ticket':
            self.app.seadb_service.update_rows(
                task.project_uuid,
                SchemaTables.TICKETS.table_name(),
                [{'pk': task.target_record_id, 'row': {
                    'last_agent_processed_at': now_iso(),
                }}],
            )
