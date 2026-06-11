"""
BaseAgent: abstract base class for all source-type-specific agent workers.

Each agent instance processes exactly one source record.
Subclasses override the abstract methods to supply:
  - the single item to process (get_item)
  - per-item context (comments, activities, etc.)
  - the record payload for the user message (build_record_payload)
  - the tool classes to register

BaseAgent expects a normalized event payload:
  - event = {type, ...}

The run() method handles all SeaDB bookkeeping (run records, action records)
and the LLM reasoning loop invocation, so subclasses only implement domain logic.

Event-centric design
----------------------
event.type resolves to one DataEventType. The resolved event type selects
the prompt module from EVENT_TYPE_PROMPTS, which determines:
  - ANALYSIS_SYSTEM_PROMPT / HANDLING_SYSTEM_PROMPT

The source-specific agent class determines the analysis terminator tool via its
ANALYSIS_TERMINATOR_TOOL attribute, and whether search tools are available via
its SEARCH_ENABLED attribute.

The user message is structured as {source_id, event, record} so the LLM always
knows exactly which event to focus on, rather than re-processing the whole record.
"""
import json
import logging
from abc import abstractmethod
from types import ModuleType

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.db.models import Project, Workspace
from seaqa_ai.utils import now_iso, uuid_str_to_32_chars, build_system_prompt
from seaqa_ai.utils.llm_api import get_llm_client_by_model_id
from seaqa_ai.utils.tools import OpenAIToolExecutor
from seaqa_ai.agent.utils.agent_runner import AgentRunner
from seaqa_ai.agent.utils.phase import AskPhase, ReactPhase
from seaqa_ai.agent.utils.agent_trace_recorder import AgentActionRecorder
from seaqa_ai.agent.tools.common_tools import FinalAnswerTool
from seaqa_ai.agent.prompts._shared import PRELUDE_SYSTEM_PROMPT
from seaqa_ai.agent.prompts._forced import FORCED_RECORD_ANALYSIS_PROMPT, FORCED_FINAL_ANSWER_PROMPT
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.agent.utils.constants import (
    DataEventType,
)
from seaqa_ai.utils.constants import AIScenario

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Abstract base class for agents.

    Manages run records in SeaDB, assembles the agent for each item,
    and delegates source-specific logic to subclasses.

    Tool assembly contract
    ----------------------
    Subclasses declare the static, source-specific tools in SOURCE_TOOL_CLASSES,
    including the analysis terminator tool declared by ANALYSIS_TERMINATOR_TOOL.
    Subclasses with dynamic tool needs should override get_tool_classes().
    """

    # Subclasses set this to the source-specific prompts package __init__,
    # which exposes PROMPTS: dict[DataEventType, ModuleType].
    EVENT_TYPE_PROMPTS: dict[DataEventType, ModuleType] = {}

    # Static, source-specific tool classes, including the analysis terminator.
    SOURCE_TOOL_CLASSES: list = []
    ANALYSIS_TERMINATOR_TOOL: type | None = None
    SEARCH_ENABLED: bool = False

    def __init__(self, app, project, event: dict | None = None):
        self.app = app
        self.seadb_api = app.seadb_api
        self.project = project
        self.project_uuid = project['uuid']
        self.agent_settings = project.get('settings', {}).get('agent', {})
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self.project_org_id = self._get_project_org_id()
        self.username_resolver = app.username_resolver
        self.last_items_processed = 0
        self.last_run_failed = False
        self.last_run_error_message = ''
        self.event = event if isinstance(event, dict) else {}
        self.event_type = self._parse_event_type(self.event)

    # ── Abstract interface ──────────────────────────────────────────

    @abstractmethod
    def get_item(self) -> dict | None:
        """Return the single source record to process, or None if not found."""
        raise NotImplementedError

    @abstractmethod
    def get_item_context(self, item: dict) -> dict:
        """Build supplementary context for a single item (e.g. comments, activities)."""
        raise NotImplementedError

    @abstractmethod
    def build_record_payload(self, item: dict, item_context: dict) -> dict:
        """Return a dict representing the current record state for the user message.

        The returned dict becomes the value of the top-level 'record' field.
        Do NOT include top-level event data here; that belongs in the event payload.
        """
        raise NotImplementedError

    def get_tool_classes(self) -> list:
        """Assemble the tool classes to register for this run."""
        return list(self.SOURCE_TOOL_CLASSES) + [FinalAnswerTool]

    @abstractmethod
    def build_record_context(self, item: dict) -> dict:
        """Build the record_context dict (source_type, source_id, source_title) for an item."""
        raise NotImplementedError

    def get_tool_registration_kwargs(self, tool_instance, record_context: dict) -> dict:
        """Return extra kwargs used when registering a tool instance.

        Subclasses can override this hook when specific tools need injected runtime
        dependencies (e.g. app/context for search tools).
        """
        return {}

    @staticmethod
    def _parse_event_type(event: dict) -> DataEventType | None:
        event_type = event.get('type')
        if not isinstance(event_type, str):
            return None
        try:
            return DataEventType(event_type)
        except ValueError:
            return None

    # ── Prompt resolution ───────────────────────────────────────────

    def _prompt_module(self) -> ModuleType | None:
        return self.EVENT_TYPE_PROMPTS.get(self.event_type)

    def get_analysis_system_prompt(self) -> str:
        module = self._prompt_module()
        if module is None:
            logger.warning('No prompt module for %s + %s', type(self).__name__, self.event_type)
            return ''
        return self._build_system_prompt(module.ANALYSIS_SYSTEM_PROMPT)

    def get_handling_system_prompt(self) -> str:
        module = self._prompt_module()
        if module is None:
            return ''
        return self._build_system_prompt(module.HANDLING_SYSTEM_PROMPT)

    @staticmethod
    def _get_tool_name(tool_cls: type) -> str:
        return tool_cls.tool['function']['name']

    def get_prelude_phase(self) -> AskPhase:
        return AskPhase(
            name='prelude',
            system_prompt=PRELUDE_SYSTEM_PROMPT,
            schema={
                'type': 'object',
                'properties': {
                    'summary': {
                        'type': 'string',
                        'description': 'The summary of event that triggered this run'
                    }
                },
                'additionalProperties': False,
                'required': ['summary']
            }
        )

    def get_analysis_phase(self) -> ReactPhase:
        return ReactPhase(
            name='analysis',
            system_prompt=self.get_analysis_system_prompt(),
            terminator_tool=self._get_tool_name(self.ANALYSIS_TERMINATOR_TOOL),
            terminator_prompt=FORCED_RECORD_ANALYSIS_PROMPT,
            max_steps=config.AGENT_MAX_ANALYSIS_STEPS,
        )

    def get_handling_phase(self) -> ReactPhase:
        return ReactPhase(
            name='handling',
            system_prompt=self.get_handling_system_prompt(),
            terminator_tool=self._get_tool_name(FinalAnswerTool),
            terminator_prompt=FORCED_FINAL_ANSWER_PROMPT,
            max_steps=config.AGENT_MAX_HANDLING_STEPS,
        )

    # ── User message assembly ───────────────────────────────────────

    def _build_llm_event_payload(self) -> dict:
        """Return the event payload exposed to the LLM user message.

        The raw event is still persisted in run records. For the LLM payload we
        intentionally omit old_value because current supported agent triggers do
        not need it and it is always null.
        """
        if not isinstance(self.event, dict):
            return {}
        return {
            key: value
            for key, value in self.event.items()
            if key != 'old_value'
        }

    def build_user_message_dict(self, item: dict, item_context: dict) -> dict:
        """Return the task payload as a plain dict.

        Structure: {source_id, event, record}
        """
        record_context = self.build_record_context(item)
        source_id = record_context.get('source_id', '')

        record = self.build_record_payload(item, item_context)

        return {
            'source_id': source_id,
            'event': self._build_llm_event_payload(),
            'record': record,
        }

    def get_executed_actions(self, source_type: str, source_id: str,
                             tool_name: str | None = None, limit: int = 10) -> list[dict]:
        """Query executed actions for a source record ordered by newest first."""
        conditions = [
            f"`source_type` = '{source_type}'",
            f"`source_id` = '{source_id}'",
            "`status` = 'executed'",
        ]
        if tool_name:
            conditions.append(f"`tool_name` = '{tool_name}'")

        where = ' AND '.join(conditions)
        sql = (
            f"SELECT `result`, `executed_at`, `created_at` "
            f"FROM `{SchemaTables.AGENT_ACTIONS.table_name()}` WHERE {where} "
            f"ORDER BY `created_at` DESC LIMIT {max(1, int(limit))}"
        )
        return self.seadb_api.query_rows(self.project_uuid, sql).get('results', [])


    # ── SeaDB run-record helpers ────────────────────────────────────

    def create_run_record(self) -> int | None:
        """Insert a new agent_runs row and return its pk.

        The events column stores the normalized event object.
        """
        events_payload = json.dumps(self.event, ensure_ascii=False)
        row = {
            'status': 'running',
            'started_at': now_iso(),
            'items_processed': 0,
            'events': events_payload,
        }
        try:
            res = self.seadb_api.insert_rows(self.project_uuid, SchemaTables.AGENT_RUNS.table_name(), [row])
            pks = res.get('pks', [])
            if len(pks) != 1:
                raise Exception(f'Expected 1 PK, got {len(pks)}')
            return pks[0]
        except Exception as e:
            logger.exception('Failed to create run record for project %s: %s', self.project_uuid, e)
            return None

    def finalize_run_record(self, run_pk: int, updates: dict):
        """Update an existing agent_runs row."""
        try:
            self.seadb_api.update_rows(
                self.project_uuid,
                SchemaTables.AGENT_RUNS.table_name(),
                [{'pk': run_pk, 'row': updates}],
            )
        except Exception as e:
            logger.exception(
                'Failed to finalize run %s for project %s: %s',
                run_pk,
                self.project_uuid,
                e,
            )

    # ── Tool executor ───────────────────────────────────────────────

    def _create_tool_executor(self, record_context: dict) -> tuple[OpenAIToolExecutor, dict]:
        """Create and configure a OpenAIToolExecutor with source-specific tools.

        Args:
            record_context: dict with source_type, source_id, source_title.

        Returns:
            (tool_executor, tool_metadata dict)
        """
        tool_executor = OpenAIToolExecutor()
        tool_metadata = {}

        for tool_cls in self.get_tool_classes():
            tool_instance = tool_cls()
            if hasattr(tool_instance, 'set_record_context'):
                tool_instance.set_record_context(record_context)

            tool_name = tool_instance.tool.get('function', {}).get('name', '')
            if tool_name:
                tool_metadata[tool_name] = {
                    'action_type': getattr(tool_instance, 'action_type', 'tool_call'),
                    'action_status': getattr(tool_instance, 'action_status', 'completed'),
                    'suggestion_text': getattr(tool_instance, 'suggestion_text', ''),
                    'get_suggestion_text': getattr(tool_instance, 'get_suggestion_text', None),
                }

            registration_kwargs = self.get_tool_registration_kwargs(tool_instance, record_context)
            tool_instance.register(tool_executor, **(registration_kwargs or {}))

        return tool_executor, tool_metadata

    @staticmethod
    def _compute_phase_tools(tool_executor: OpenAIToolExecutor, spec: ReactPhase, *,
                             exclude_terminators: set, filter_search: bool):
        tools = [
            tool
            for tool in tool_executor.get()
            if tool.get('function', {}).get('name') not in exclude_terminators
        ]
        if filter_search:
            tools = [
                tool for tool in tools
                if not tool.get('function', {}).get('name', '').endswith('_search')
            ]
        spec.tools = tools

    def _get_project_org_id(self):
        with self.db_session_class() as db_session:
            try:
                project_uuid_32 = uuid_str_to_32_chars(self.project_uuid)
                project_metadata = Project.get_project_metadata(db_session, project_uuid_32)
                workspace_id = project_metadata.get('workspace_id')
                if workspace_id is None:
                    return None
                return Workspace.get_org_id_by_workspace_id(db_session, workspace_id)
            except Exception as e:
                logger.warning(f'Failed to resolve org_id for project {self.project_uuid}: {e}')

    def _build_ai_stats_context(self) -> dict:
        """Build context used for AI usage statistics logging."""
        return {
            'org_id': self.project_org_id if self.project_org_id is not None else -1,
            'project_uuid': self.project_uuid,
            'scenario': AIScenario.AGENT.value,
        }

    def _build_system_prompt(self, base_prompt: str) -> str:
        """Merge project-level prompt from project settings with base prompt."""
        project_prompt = (self.project.get('settings') or {}).get('prompt', '')
        return build_system_prompt(base_prompt, project_prompt)

    # ── Per-item processing ─────────────────────────────────────────

    def process_item(self, item: dict, run_id: int) -> str:
        """Run the AI reasoning loop for a single source record.

        Builds context, creates an AgentRunner with the appropriate tools and prompts,
        and runs it. Actions are recorded to SeaDB via create_action_record.
        """
        record_context = self.build_record_context(item)
        item_context = self.get_item_context(item)
        task_dict = self.build_user_message_dict(item, item_context)

        tool_executor, tool_metadata = self._create_tool_executor(record_context)
        llm_client = get_llm_client_by_model_id(self.app.data_logger, self.agent_settings.get('model'))

        analysis_phase = self.get_analysis_phase()
        handling_phase = self.get_handling_phase()
        if not analysis_phase.system_prompt or not handling_phase.system_prompt:
            logger.warning(f'No analysis or handling system prompt for {record_context.get("source_type")}. Skip running agent.')
            return

        self._compute_phase_tools(
            tool_executor, analysis_phase,
            exclude_terminators={handling_phase.terminator_tool},
            filter_search=not self.SEARCH_ENABLED,
        )
        self._compute_phase_tools(
            tool_executor, handling_phase,
            exclude_terminators={analysis_phase.terminator_tool},
            filter_search=False,
        )

        agent_runner = AgentRunner(
            tool_executor=tool_executor,
            llm_client=llm_client,
            record_context=record_context,
            ai_stats_context=self._build_ai_stats_context(),
            action_recorder=AgentActionRecorder(self.project_uuid, self.seadb_api),
            run_id=run_id,
            tool_metadata=tool_metadata,
            prelude_phase=self.get_prelude_phase(),
            analysis_phase=analysis_phase,
            handling_phase=handling_phase,
        )

        try:
            agent_runner.run(task_dict)
        except Exception as e:
            logger.exception(
                f'Agent failed for {record_context.get("source_type")} '
                f'{record_context.get("source_id")}: {e}'
            )
            raise

    def on_run_finished(self, item: dict, success: bool):
        """Hook called after the item run is finalized."""
        return

    # ── Main entry point ────────────────────────────────────────────

    def run(self) -> int | None:
        """Execute the agent workflow for a single item."""
        self.last_items_processed = 0
        self.last_run_failed = False
        self.last_run_error_message = ''
        run_pk = None
        item = None

        try:
            item = self.get_item()
            if item is not None:
                if self.event_type is None:
                    self.last_run_failed = True
                    self.last_run_error_message = (
                        'No recognised event type in event payload; refusing to run.'
                    )
                    logger.warning(
                        'Refusing to run agent for project %s: %s event=%s',
                        self.project_uuid, self.last_run_error_message, self.event,
                    )
                    return None
                try:
                    run_pk = self.create_run_record()
                    if run_pk is None:
                        self.last_run_failed = True
                        self.last_run_error_message = 'Failed to create run record'
                        return None
                    self.process_item(item, run_pk)
                except Exception as e:
                    self.last_run_failed = True
                    self.last_run_error_message = str(e)[:1000]
                    logger.exception(f'Error processing item: {e}')

        except Exception as e:
            logger.exception(f'Error running agent for project {self.project_uuid}: {e}')
            self.last_run_failed = True
            self.last_run_error_message = str(e)[:1000]
        finally:
            self.last_items_processed = 1 if run_pk is not None and not self.last_run_failed else 0
            if run_pk is not None:
                final_status = 'failed' if self.last_run_failed else 'completed'
                self.finalize_run_record(
                    run_pk,
                    {
                        'status': final_status,
                        'finished_at': now_iso(),
                        'items_processed': self.last_items_processed,
                        'error_message': self.last_run_error_message,
                    },
                )
            if item is not None:
                try:
                    self.on_run_finished(item, success=not self.last_run_failed)
                except Exception as e:
                    logger.exception(
                        'Post-run hook failed for project %s record_id=%s: %s',
                        self.project_uuid,
                        item.get('_pk'),
                        e,
                    )

        return run_pk
