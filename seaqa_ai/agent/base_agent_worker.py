"""
BaseAgentWorker: abstract base class for all source-type-specific agent workers.

Subclasses override the abstract methods to supply:
  - the list of items to process (tickets, github issues, etc.)
  - per-item context (comments, activities, etc.)
  - the LLM message describing the item
  - the tool classes to register
  - the system prompt

The run() method handles all SeaDB bookkeeping (run records, action records)
and the LLM reasoning loop invocation, so subclasses only implement domain logic.
"""
import logging
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Optional

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.utils.assignee_resolver import AssigneeResolver
from seaqa_ai.utils.llm_api import get_llm_client_by_model_id, CompletionAPI
from seaqa_ai.utils.tools import SimpleToolExecutor
from seaqa_ai.agent.base_agent import BaseAgent
from seaqa_ai.agent.tools.common_tools import FinalAnswerTool

logger = logging.getLogger(__name__)

AGENT_RUNS_TABLE = 'agent_runs'
AGENT_ACTIONS_TABLE = 'agent_actions'


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(dt_str):
    if not dt_str:
        return None
    try:
        if dt_str.endswith('Z'):
            dt_str = dt_str[:-1] + '+00:00'
        return datetime.fromisoformat(dt_str)
    except Exception as e:
        logger.exception(f'Error parsing datetime: {e}')
        return None


class BaseAgentWorker:
    """
    Abstract base class for agent workers.

    Manages run records in SeaDB, assembles the BaseAgent for each item,
    and delegates source-specific logic to subclasses.
    """

    def __init__(self, app, project):
        self.app = app
        self.seadb_api = app.seadb_api
        self.project = project
        self.project_uuid = project['uuid']
        self.agent_settings = project.get('settings', {}).get('agent', {})
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self._assignee_display_cache = {}
        self.assignee_resolver = AssigneeResolver(
            db_session_class=self.db_session_class,
            cache=self._assignee_display_cache,
        )
        self.last_items_processed = 0
        self.last_run_failed = False
        self.last_run_error_message = ''

    # ── Abstract interface ──────────────────────────────────────────

    @abstractmethod
    def get_items_to_process(self, since) -> list[dict]:
        """Return the list of source records to process in this run."""
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
            return _parse_datetime(rows[0]['started_at'])
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
            'content': content or '',
            'result': result or '',
            'status': action_status,
            'suggestion_text': suggestion_text or '',
            'created_at': _now_iso(),
        }
        res = self.seadb_api.insert_rows(self.project_uuid, AGENT_ACTIONS_TABLE, [row])
        pks = res.get('pks', [])
        if len(pks) != 1:
            raise Exception(f'Expected 1 PK, got {len(pks)}')
        return pks[0]

    # ── Tool executor ───────────────────────────────────────────────

    def _create_tool_executor(self, record_context: dict) -> tuple[SimpleToolExecutor, dict]:
        """Create and configure a SimpleToolExecutor with source-specific tools.

        Args:
            record_context: dict with source_type, source_id, source_title.

        Returns:
            (tool_executor, tool_metadata dict)
        """
        tool_executor = SimpleToolExecutor()
        tool_metadata = {}

        for tool_cls in self.get_tool_classes():
            tool_instance = tool_cls(self.app)
            if hasattr(tool_instance, 'set_record_context'):
                tool_instance.set_record_context(record_context)

            tool_name = tool_instance.tool.get('function', {}).get('name', '')
            if tool_name:
                tool_metadata[tool_name] = {
                    'action_type': getattr(tool_instance, 'action_type', 'tool_call'),
                    'action_status': getattr(tool_instance, 'action_status', 'completed'),
                    'suggestion_text': getattr(tool_instance, 'suggestion_text', ''),
                }

            tool_instance.register(tool_executor)

        return tool_executor, tool_metadata

    def _get_llm_client(self) -> CompletionAPI:
        model_id = self.agent_settings.get('model', 'gemini-2.5-flash')
        return get_llm_client_by_model_id(self.app.data_logger, model_id)

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
        llm_client = self._get_llm_client()

        agent = BaseAgent(
            tool_executor=tool_executor,
            llm_client=llm_client,
            record_context=record_context,
            action_recorder=self.create_action_record,
            run_id=run_id,
            tool_metadata=tool_metadata,
            system_prompt=self.get_system_prompt(),
            max_steps=config.AGENT_MAX_STEPS_PER_TICKET,
        )

        try:
            return agent.run(item_message)
        except Exception as e:
            logger.exception(
                f'Agent failed for {record_context.get("source_type")} '
                f'{record_context.get("source_id")}: {e}'
            )
            raise

    # ── Main entry point ────────────────────────────────────────────

    def run(self, run_pk: int):
        """Execute the full agent workflow.

        Args:
            run_pk: The run id (created by coordinator) to which actions are appended.
                The coordinator is responsible for finalizing the run record.
        """
        self.last_items_processed = 0
        self.last_run_failed = False
        self.last_run_error_message = ''
        items_processed = 0

        try:
            last_run_time = self.get_last_run_time()
            all_items = self.get_items_to_process(last_run_time)

            for item in all_items:
                try:
                    self.process_item(item, run_pk)
                    items_processed += 1
                except Exception as e:
                    logger.exception(f'Error processing item: {e}')
                    continue

            self.last_items_processed = items_processed

        except Exception as e:
            logger.exception(f'Error running agent for project {self.project_uuid}: {e}')
            self.last_items_processed = items_processed
            self.last_run_failed = True
            self.last_run_error_message = str(e)[:1000]

        return run_pk
