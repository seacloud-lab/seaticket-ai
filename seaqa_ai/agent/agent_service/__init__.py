"""Top-level orchestration for one typed agent task."""
import json
import logging

from seaqa_ai.agent.models import AgentTask
from seaqa_ai.agent.tools.engage_phase import EngagePhase
from seaqa_ai.agent.tools.get_record import GetRecord
from seaqa_ai.agent.utils.react import AgentReACT
from seaqa_ai.agent.utils.react.tracer import AgentReACTTracer
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.utils import now_iso
from seaqa_ai.utils.llm_api import get_responses_client_by_model_id


logger = logging.getLogger(__name__)

TOP_LEVEL_MAX_STEPS = 5
FINAL_OUTPUT_SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {
            'type': 'string',
            'description': 'A concise, factual summary of the completed or skipped phases and outcome.',
        },
    },
    'required': ['summary'],
    'additionalProperties': True,
}


class AgentService:
    """Execute one task while keeping workflow decisions in the top-level agent."""

    def __init__(self, app, project):
        self.app = app
        self.project = project

    def run(self, task: AgentTask) -> dict:
        if not isinstance(task, AgentTask):
            raise TypeError('task must be an AgentTask')

        run_id = self._create_run_record(task)
        tracer = AgentReACTTracer(task.project_uuid, self.app.seadb_api)
        traces_flushed = False
        record_cache = {}
        try:
            target_record = GetRecord().execute(
                source_type=task.target_source_type,
                connection_id=task.target_connection_id,
                record_id=task.target_record_id,
                project_uuid=task.project_uuid,
                app=self.app,
                record_cache=record_cache,
            )
            context = self._build_context(task, run_id, target_record)
            context['record_cache'] = record_cache
            message_input = self._build_message_input(task)
            responses_client = get_responses_client_by_model_id(
                self.app.data_logger,
                ((self.project.get('settings') or {}).get('agent') or {}).get('model'),
            )
            result = AgentReACT(
                app=self.app,
                responses_client=responses_client,
                system_prompt=self._get_system_prompt(),
                message_input=message_input,
                tools=[EngagePhase],
                react_tracer=tracer,
                final_output_schema=FINAL_OUTPUT_SCHEMA,
                max_steps=TOP_LEVEL_MAX_STEPS,
                context=context
            ).run()
            result = dict(result)
            result.update({'run_id': run_id, 'status': 'completed'})
            self._finalize_run_record(
                task.project_uuid, run_id, status='completed', items_processed=1,
            )
            pending_suggestion_action_ids = tracer.flush()
            traces_flushed = True
            self._trigger_auto_actions(task.project_uuid, pending_suggestion_action_ids)
            return result
        except Exception as error:
            self._finalize_run_record(
                task.project_uuid,
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
            if not traces_flushed:
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

    @staticmethod
    def _build_context(task, run_id, target_record):
        source_id = (
            task.target_record_id
            if task.target_connection_id is None
            else f'{task.target_connection_id}_{task.target_record_id}'
        )
        return {
            'project_uuid': task.project_uuid,
            'run_id': run_id,
            'task_id': task.task_id,
            'idempotency_key': task.idempotency_key,
            'source_type': task.target_source_type,
            'source_id': source_id,
            'source_title': target_record.get('title', ''),
            'target_record': target_record,
            'triggering_event': task.trigger_event.event,
        }

    @staticmethod
    def _build_message_input(task):
        event = task.trigger_event
        return json.dumps({
            'task_id': task.task_id,
            'idempotency_key': task.idempotency_key,
            'triggering_event': {
                'event_id': event.event_id,
                'correlation_id': event.correlation_id,
                'causation_id': event.causation_id,
                'event_type': event.event_type,
                'event': event.event,
                'modified_time': event.modified_time.isoformat() if event.modified_time else None,
                'received_at': event.received_at.isoformat(),
            },
            'records_input': [{
                'source_type': task.target_source_type,
                'connection_id': task.target_connection_id,
                'record_id': task.target_record_id,
            }],
        }, ensure_ascii=False)

    @staticmethod
    def _get_system_prompt():
        from pathlib import Path

        return Path(__file__).with_name('system_prompt.xml').read_text(encoding='utf-8')

    def _create_run_record(self, task):
        response = self.app.seadb_api.insert_rows(
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

    def _finalize_run_record(self, project_uuid, run_id, *, status, items_processed, error_message=''):
        self.app.seadb_api.update_rows(
            project_uuid,
            SchemaTables.AGENT_RUNS.table_name(),
            [{'pk': run_id, 'row': {
                'status': status,
                'finished_at': now_iso(),
                'items_processed': items_processed,
                'error_message': error_message,
            }}],
        )
