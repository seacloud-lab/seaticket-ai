"""
AgentWorker: Processes tickets for a single project.

This module implements the core ticket processing logic:
1. Query tickets from SeaDB that need attention
2. Run AI analysis on each ticket using TicketAgent
3. Record actions and results back to SeaDB
"""
import logging
from datetime import datetime, timezone, timedelta

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.utils.assignee_resolver import AssigneeResolver
from seaqa_ai.utils.llm_api import get_llm_client_by_model_id
from seaqa_ai.utils.tools import SimpleToolExecutor
from seaqa_ai.agent.ticket_agent import TicketAgent
from seaqa_ai.agent.tools.agent_tools import AGENT_TOOL_CLASSES
from seaqa_ai.utils.llm_api import CompletionAPI

logger = logging.getLogger(__name__)

AGENT_RUNS_TABLE = 'agent_runs'
AGENT_ACTIONS_TABLE = 'agent_actions'
TICKETS_TABLE = 'tickets'
TICKET_COMMENTS_TABLE = 'ticket_comments'
TICKET_ACTIVITIES_TABLE = 'ticket_activities'

DEFAULT_SYSTEM_PROMPT = """You are an autonomous agent that helps manage tickets in a project management system.
You run periodically to analyze tickets and take actions to help the team stay on top of their work.

For each ticket provided, follow these steps strictly in order, ONE tool call per step:
1. Call analyze_ticket to record your findings about the ticket's current state, any issues, and what action you plan to take.
2. Optionally call notify_assignee if there is a genuine reason (e.g., approaching due date, long inactivity).
3. Optionally call add_comment to suggest a reply or update comment on the ticket.
4. Call final_answer to provide a brief summary of what was done. This MUST be the last tool call.

Rules:
- Always call analyze_ticket first before any other actions.
- Call only ONE tool per step. Never call multiple tools in the same step.
- final_answer MUST be called alone, never together with any other tool in the same step.
- Once you call final_answer, you are done. Do NOT call any more tools after final_answer.
- Only notify assignees when there is a genuine reason (approaching the deadline, long inactivity, etc.)
- Comment suggestions should be helpful and actionable.
- Be concise in your analysis and only state the most essential points.
- Do NOT repeat the analysis or actions after calling final_answer.
"""


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


class AgentWorker:
    """
    Processes tickets for a single project.

    This class:
    1. Queries tickets from SeaDB that need attention
    2. Uses TicketAgent to analyze each ticket via LLM function calling
    3. Records all actions to SeaDB for auditing and user review
    """

    _DEFAULT_NOTIFY_BEFORE_DUE_HOURS = 48

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
            cache=self._assignee_display_cache
        )

    @property
    def notify_before_due_hours(self) -> int:
        """Hours before due date to notify assignees (from project agent config)."""
        return int(
            self.agent_settings.get(
                'notify_before_due_hours', self._DEFAULT_NOTIFY_BEFORE_DUE_HOURS
            )
        )

    # ── SeaDB helpers ──────────────────────────────────────────────

    def get_last_run_time(self):
        """Query the last run time from the agent_runs table."""
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
    
    def create_run_record(self):
        """Insert a new agent_runs row with status='running'. Returns _pk."""
        row = {
            'status': 'running',
            'started_at': _now_iso(),
            'tickets_processed': 0
        }
        res = self.seadb_api.insert_rows(self.project_uuid, AGENT_RUNS_TABLE, [row])
        pks = res.get('pks', [])
        if len(pks) != 1:
            raise Exception(f'Expected 1 PK, got {len(pks)}')
        return pks[0]

    def update_run_record(self, run_pk, updates):
        """Update an existing agent_runs row."""
        self.seadb_api.update_rows(
            self.project_uuid,
            AGENT_RUNS_TABLE,
            [{
                'pk': run_pk,
                'row': updates
            }]
        )
    
    def create_action_record(self, run_id, ticket_id, ticket_title,
                            action_type, tool_name, content, result, action_status,
                            suggestion_text=''):
        """Insert a new agent_actions row. Returns _pk."""
        row = {
            'run_id': run_id,
            'ticket_id': ticket_id,
            'ticket_title': ticket_title or '',
            'action_type': action_type,
            'tool_name': tool_name or '',
            'content': content or '',
            'result': result or '',
            'status': action_status,
            'suggestion_text': suggestion_text or '',
            'created_at': _now_iso()
        }
        res = self.seadb_api.insert_rows(self.project_uuid, AGENT_ACTIONS_TABLE, [row])
        pks = res.get('pks', [])
        if len(pks) != 1:
            raise Exception(f'Expected 1 PK, got {len(pks)}')
        return pks[0]
    
    # ── Ticket queries ─────────────────────────────────────────────

    def get_changed_tickets(self, since):
        """
        Retrieve tickets that have been modified since ``since`` and are not closed/deleted.
        """
        conditions = []
        if since:
            since_iso = since.isoformat()
            conditions.append(f'`modified_time` > "{since_iso}"')

        conditions.append("(`state` != 'closed' OR `state` IS NULL)")
        conditions.append("(`deleted` = False OR `deleted` IS NULL)")

        where = ' AND '.join(conditions)
        sql = f"SELECT * From `{TICKETS_TABLE}` WHERE {where} ORDER BY `modified_time` DESC LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        res = self.seadb_api.query_rows(self.project_uuid, sql)
        return res.get('results', [])

    def get_due_soone_tickets(self, hours=48):
        """Retrieve open tickets whose due_date is within ``hours`` hours from now."""
        now = datetime.now(timezone.utc)
        threshold = (now + timedelta(hours=hours)).isoformat()
        sql = (
            f"SELECT * From `{TICKETS_TABLE}` "
            f"WHERE `due_date` IS NOT NULL AND `due_date` < '{threshold}' "
            f"AND (`state` != 'closed' OR `state` IS NULL) "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"ORDER BY `due_date` ASC LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        res = self.seadb_api.query_rows(self.project_uuid, sql)
        return res.get('results', [])

    def get_ticket_context(self, ticket_id):
        """
        Build full context for a ticket: the ticket itself, its comments,
        and its activity history.
        """
        # Comments
        comments_sql = (
            f"SELECT * From `{TICKET_COMMENTS_TABLE}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"ORDER BY `_pk` ASC LIMIT 100"
        )
        comments = self.seadb_api.query_rows(self.project_uuid, comments_sql).get('results', [])

        # Activities
        activities_sql = (
            f"SELECT * FROM `{TICKET_ACTIVITIES_TABLE}` "
            f"WHERE `ticket_id` = {ticket_id} "
            f"ORDER BY `created_time` ASC LIMIT 100"
        )
        activities = self.seadb_api.query_rows(self.project_uuid, activities_sql).get('results', [])

        return {
            'comments': comments,
            'activities': activities
        }
    
    # ── AI reasoning ───────────────────────────────────────────────

    def _build_ticket_message(self, ticket, ticket_context):
        """Build the user message describing the ticket for AI analysis."""
        parts = [
            f"## Ticket #{ticket['_pk']}: {ticket.get('title', 'Untitled')}",
            f"- State: {ticket.get('state', 'N/A')}",
            f"- Substate: {ticket.get('substate', 'N/A')}",
            f"- Priority: {ticket.get('priority', 'N/A')}",
            f"- Assignees: {self.assignee_resolver.resolve_assignees_for_display(ticket.get('assignees'))}",
            f"- Created: {ticket.get('created_time', 'N/A')}",
            f"- Last modified: {ticket.get('modified_time', 'N/A')}",
            f"- Due date: {ticket.get('due_date', 'N/A')}",
        ]
        if ticket.get('content'):
            parts.append(f"\n### Content\n{ticket['content'][:5000]}")

        if ticket_context.get('comments'):
            parts.append("\n### Recent 10 Comments")
            for c in ticket_context['comments'][-10:]:
                creator_display = self.assignee_resolver.resolve_user_for_display(c.get('creator', '?'))
                parts.append(f"- [{creator_display or '?'} @ {c.get('created_time', '?')}]: {(c.get('content') or '')}")

        if ticket_context.get('activities'):
            parts.append("\n### Recent 10 Activities")
            for a in ticket_context['activities'][-10:]:
                detail_display = self.assignee_resolver.convert_activity_detail(a.get('detail', ''))
                parts.append(f"- [{a.get('activity_type', '?')} @ {a.get('created_time', '?')}]: {detail_display}")

        return '\n'.join(parts)

    def _create_tool_executor(self, ticket_context=None) -> tuple[SimpleToolExecutor, dict]:
        """Create and configure a SimpleToolExecutor with agent tools.

        Args:
            ticket_context: Dict with ticket_id, ticket_title to inject into tools

        Returns:
            tuple: (SimpleToolExecutor, tool_metadata dict)
        """
        tool_executor = SimpleToolExecutor()
        tool_metadata = {}
        
        # Register all agent tools and set their ticket context
        for tool_cls in AGENT_TOOL_CLASSES:
            tool_instance = tool_cls(self.app)
            if ticket_context and hasattr(tool_instance, 'set_ticket_context'):
                tool_instance.set_ticket_context(ticket_context)

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
        """Get the LLM client for the agent."""
        model_id = self.agent_settings.get('model', 'gemini-2.5-flash')
        return get_llm_client_by_model_id(self.app.data_logger, model_id)

    def process_ticket(self, ticket, run_id) -> str:
        """
        Run the AI reasoning loop for a single ticket.

        This method:
        1. Builds context for the ticket
        2. Creates a TicketAgent with registered tools
        3. Runs the agent to analyze the ticket
        4. Actions are automatically recorded via the agent's callback
        """
        ticket_id = ticket['_pk']
        ticket_title = ticket.get('title', 'Untitled')
        ticket_context_data = self.get_ticket_context(ticket_id)

        # Build the task message
        ticket_message = self._build_ticket_message(ticket, ticket_context_data)

        # Create ticket context for tool injection
        ticket_context = {
            'ticket_id': ticket_id,
            'ticket_title': ticket_title,
        }

        # Create tool executor and register tools with ticket context
        tool_executor, tool_metadata = self._create_tool_executor(ticket_context)

        # Get LLM client
        llm_client = self._get_llm_client()

        # Create the agent with action recording callback and custom system prompt
        agent = TicketAgent(
            tool_executor=tool_executor,
            llm_client=llm_client,
            ticket_context=ticket_context,
            action_recorder=self.create_action_record,
            run_id=run_id,
            tool_metadata=tool_metadata,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            max_steps=config.AGENT_MAX_STEPS_PER_TICKET,
        )

        try:
            result = agent.run(ticket_message)
            return result
        except Exception as e:
            logger.exception(f'Agent failed for ticket #{ticket_id}: {e}')
            # Error is already recorded by the agent
            raise

    # ── Main entry point ───────────────────────────────────────────

    def run(self) -> int:
        """Execute the full agent workflow for this project."""
        run_pk = self.create_run_record()
        tickets_processed = 0

        try:
            last_run_time = self.get_last_run_time()

            # Gather tickets to process
            changed_tickets = self.get_changed_tickets(last_run_time)
            due_soon_tickets = self.get_due_soone_tickets(self.notify_before_due_hours)

            # Merge and deduplicate by _pk
            ticket_map = {}
            for t in changed_tickets + due_soon_tickets:
                ticket_map[t['_pk']] = t
            all_tickets = list(ticket_map.values())[:config.AGENT_MAX_TICKETS_PER_RUN]

            for ticket in all_tickets:
                try:
                    self.process_ticket(ticket, run_pk)
                    tickets_processed += 1
                except Exception as e:
                    logger.exception(f'Error processing ticket #{ticket["_pk"]}: {e}')
                    continue

            # Update run record
            self.update_run_record(run_pk, {
                'status': 'completed',
                'finished_at': _now_iso(),
                'tickets_processed': tickets_processed,
            })

        except Exception as e:
            logger.exception(f'Error running agent for project {self.project_uuid}: {e}')
            self.update_run_record(run_pk, {
                'status': 'failed',
                'finished_at': _now_iso(),
                'tickets_processed': tickets_processed,
                'error_message': str(e)[:1000]
            })
        
        return run_pk
