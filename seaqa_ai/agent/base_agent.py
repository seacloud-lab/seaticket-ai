"""
BaseAgent: abstract base class for all source-type-specific agent workers.

Each agent instance processes exactly one source record.
Subclasses override the abstract methods to supply:
  - the single item to process (get_item)
  - per-item context (comments, activities, etc.)
  - the LLM message describing the item
  - the tool classes to register
  - the system prompt

The run() method handles all SeaDB bookkeeping (run records, action records)
and the LLM reasoning loop invocation, so subclasses only implement domain logic.
"""
import logging
from abc import abstractmethod

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.db.models import Project, Workspace
from seaqa_ai.utils import parse_datetime, now_iso, uuid_str_to_32_chars
from seaqa_ai.utils.llm_api import get_llm_client_by_model_id, CompletionAPI
from seaqa_ai.utils.tools import OpenAIToolExecutor
from seaqa_ai.agent.utils.agent_runner import AgentRunner
from seaqa_ai.agent.utils.constants import AGENT_RUNS_TABLE, AGENT_ACTIONS_TABLE, MAX_CONTENT_CHARS
from seaqa_ai.utils.constants import AIScenario

logger = logging.getLogger(__name__)


class BaseAgent:
    """
    Abstract base class for agents.

    Manages run records in SeaDB, assembles the agent for each item,
    and delegates source-specific logic to subclasses.
    """

    def __init__(self, app, project):
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
    def build_item_message(self, item: dict, item_context: dict) -> str:
        """Construct the user message sent to the LLM for a single item."""
        raise NotImplementedError

    @abstractmethod
    def get_tool_classes(self) -> list:
        """Return the list of tool classes to register for this source type."""
        raise NotImplementedError

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for the LLM reasoning loop."""
        raise NotImplementedError

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

    # ── SeaDB run-record helpers ────────────────────────────────────

    def get_last_run_time(self):
        """Return the started_at datetime of the most recently completed run, or None."""
        sql = (
            f"SELECT * FROM `{AGENT_RUNS_TABLE}` "
            f"WHERE `status` = 'completed' "
            f"ORDER BY `started_at` DESC LIMIT 1"
        )
        result = self.seadb_api.query_rows(self.project_uuid, sql)
        rows = result.get('results', [])
        if rows:
            return parse_datetime(rows[0]['started_at'])
        return None


    def create_action_record(
        self,
        run_id,
        source_type,
        source_id,
        source_title,
        action_type,
        tool_name,
        content,
        result,
        action_status,
        suggestion_text='',
    ):
        """Insert a new agent_actions row. Returns _pk."""
        row = {
            'run_id': run_id,
            'source_type': source_type or '',
            'source_id': str(source_id) if source_id is not None else '',
            'source_title': source_title or '',
            'action_type': action_type,
            'tool_name': tool_name or '',
            'content': content[:MAX_CONTENT_CHARS] if content is not None else '',
            'result': result[:MAX_CONTENT_CHARS] if result is not None else '',
            'status': action_status,
            'suggestion_text': suggestion_text or '',
            'created_at': now_iso(),
        }
        res = self.seadb_api.insert_rows(self.project_uuid, AGENT_ACTIONS_TABLE, [row])
        pks = res.get('pks', [])
        if len(pks) != 1:
            raise Exception(f'Expected 1 PK, got {len(pks)}')
        return pks[0]

    def create_run_record(self) -> int | None:
        """Insert a new agent_runs row and return its pk."""
        row = {
            'status': 'running',
            'started_at': now_iso(),
            'items_processed': 0,
        }
        try:
            res = self.seadb_api.insert_rows(self.project_uuid, AGENT_RUNS_TABLE, [row])
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
                AGENT_RUNS_TABLE,
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
                }

            registration_kwargs = self.get_tool_registration_kwargs(tool_instance, record_context)
            tool_instance.register(tool_executor, **(registration_kwargs or {}))

        return tool_executor, tool_metadata

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
            'username': 'agent',
            'org_id': self.project_org_id if self.project_org_id is not None else -1,
            'project_uuid': self.project_uuid,
            'scenario': AIScenario.AGENT.value,
        }

    # ── Per-item processing ─────────────────────────────────────────

    def process_item(self, item: dict, run_id: int) -> str:
        """Run the AI reasoning loop for a single source record.

        Builds context, creates a BaseAgent with the appropriate tools, and runs it.
        Actions are recorded to SeaDB via create_action_record.
        """
        record_context = self.build_record_context(item)
        item_context = self.get_item_context(item)
        item_message = self.build_item_message(item, item_context)

        tool_executor, tool_metadata = self._create_tool_executor(record_context)
        llm_client = get_llm_client_by_model_id(self.app.data_logger, self.agent_settings.get('model'))

        agent_runner = AgentRunner(
            tool_executor=tool_executor,
            llm_client=llm_client,
            record_context=record_context,
            ai_stats_context=self._build_ai_stats_context(),
            action_recorder=self.create_action_record,
            run_id=run_id,
            tool_metadata=tool_metadata,
            system_prompt=self.get_system_prompt(),
            max_steps=config.AGENT_MAX_STEPS,
        )

        try:
            return agent_runner.run(item_message)
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
