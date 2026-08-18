"""AgentManager for event listening, periodic scanning, and task enqueueing."""
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from redis.exceptions import ConnectionError as NoMQAvailable, ResponseError, TimeoutError

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.db.models import ProjectConnection
from seaqa_ai.agent.utils import compact_events
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.agent.utils.constants import (
    AGENT_TASK_QUEUE_NAME,
    DATA_EVENTS_CHANNEL,
    DataEventType,
    AgentSourceType,
)

logger = logging.getLogger(__name__)


@dataclass
class PendingEventState:
    project_uuid: str
    record_id: int
    events: list = field(default_factory=list)
    first_event_time: float = 0.0
    last_enqueue_time: float = 0.0


class AgentManager:
    """Listen to events and enqueue agent tasks."""

    _retry_interval = 3
    _no_message_timeout = 5 * 60 # 5 minutes
    _CONNECTION_TABLE_GETTERS = {
        AgentSourceType.GITHUB_ISSUE.value: SchemaTables.GITHUB_ISSUES.table_name,
        AgentSourceType.DISCOURSE_FORUM.value: SchemaTables.DISCOURSE_TOPICS.table_name,
        AgentSourceType.EMAIL.value: SchemaTables.THREAD.table_name,
        AgentSourceType.GENERAL_TASK.value: SchemaTables.GENERAL_TASK.table_name,
    }

    def __init__(self, app):
        self.app = app
        self.mq = app.redis_client.connection
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self.should_stop = threading.Event()
        self.worker_list = []
        self.pending_events: dict[str, PendingEventState] = {}

    def start(self):
        if not self.mq:
            logger.warning('Redis is unavailable, agent manager will not start.')
            return

        event_listener = threading.Thread(
            target=self._event_listener,
            name='agent_event_listener',
            daemon=True,
        )
        event_listener.start()
        self.worker_list.append(event_listener)

        due_scanner = threading.Thread(
            target=self._due_scanner,
            name='agent_due_scanner',
            daemon=True,
        )
        due_scanner.start()
        self.worker_list.append(due_scanner)

        logger.info('Agent manager started.')

    def stop(self):
        self.should_stop.set()
        for worker in self.worker_list:
            worker.join(timeout=2)
        logger.info('Agent Manager stopped.')

    def add_to_task_queue(
        self,
        project_uuid: str,
        source_type: str,
        record_id: int,
        connection_id: int | None,
        event: dict | None,
    ) -> dict:
        """Add one task message into the agent task queue."""
        project_uuid = uuid_str_to_32_chars(project_uuid)
        if not project_uuid:
            raise ValueError('project_uuid is required')

        message = {
            'project_uuid': project_uuid,
            'source_type': source_type,
            'record_id': record_id,
            'event': event if isinstance(event, dict) else {},
        }
        if connection_id is not None:
            message['connection_id'] = connection_id

        self.mq.lpush(AGENT_TASK_QUEUE_NAME, json.dumps(message))
        logger.info(
            'Added agent task to queue for project %s (source=%s record_id=%s connection_id=%s event=%s)',
            project_uuid,
            source_type,
            record_id,
            connection_id,
            message['event'],
        )
        return message

    def _event_listener(self):
        while not self.should_stop.is_set():
            try:
                self._listen_once()
            except (ResponseError, NoMQAvailable, TimeoutError) as e:
                logger.error('Redis pubsub failure in agent manager: %s', e)
            except Exception as e:
                logger.exception('Agent manager event listener failed: %s', e)

            if self.should_stop.wait(self._retry_interval):
                return

    def _listen_once(self):
        with self.mq.pubsub(ignore_subscribe_messages=True) as pubsub:
            pubsub.subscribe(DATA_EVENTS_CHANNEL)
            logger.info('Agent manager starting listen on channel: %s', DATA_EVENTS_CHANNEL)
            last_message_time = time.time()
            last_pending_check_time = time.time()

            while not self.should_stop.is_set():
                message = pubsub.get_message(timeout=1.0)
                now_time = time.time()

                if message is not None and isinstance(message.get('data'), str):
                    last_message_time = now_time
                    self._handle_event_message(message.get('data'), now_time)

                if (now_time - last_pending_check_time) >= config.AGENT_ENQUEUE_PENDING_EVENTS_CHECK_INTETVAL:
                    last_pending_check_time = now_time
                    self._flush_pending_records(now_time)

                if message is None and (now_time - last_message_time) >= self._no_message_timeout:
                    logger.warning('No pubsub messages for %ss, reconnecting.', self._no_message_timeout)
                    return

    def _handle_event_message(self, raw_data: str, now_time: float):
        try:
            data = json.loads(raw_data)
        except Exception:
            logger.warning('Ignore invalid event payload: %s', raw_data)
            return
        if not isinstance(data, dict):
            logger.warning('Ignore non-dict event payload: %s', raw_data)
            return

        event = data.get('event')
        if not isinstance(event, dict):
            event = {}

        event_type = (event or {}).get('type')
        if not DataEventType.is_valid(event_type):
            logger.debug('Ignore unknown event_type: %s', event_type)
            return

        source_type = data.get('source_type')
        if not AgentSourceType.is_valid(source_type):
            logger.warning('Ignore unknown source_type: %s', source_type)
            return

        if self._is_noop_event(source_type, event):
            logger.warning(
                'Ignore no-op event (source=%s type=%s) for record %s',
                source_type, event_type, data.get('record_id'),
            )
            return

        project_uuid = data.get('project_uuid')
        if not project_uuid:
            logger.warning('Ignore event without project_uuid: %s', raw_data)
            return
        project_uuid = uuid_str_to_32_chars(str(project_uuid).strip())
        if not project_uuid:
            logger.warning('Ignore event with invalid project_uuid: %s', raw_data)
            return

        modified_time = data.get('modified_time')
        if modified_time is not None:
            try:
                mt = datetime.fromisoformat(str(modified_time))
                if mt.tzinfo is None:
                    mt = mt.replace(tzinfo=timezone.utc)
                if datetime.now(tz=timezone.utc) - mt > timedelta(days=7):
                    logger.debug('Ignore event with modified_time older than 7 days: %s', modified_time)
                    return
            except (ValueError, TypeError):
                logger.warning('Ignore event with invalid modified_time: %s', modified_time)
                return

        project = self.app.seaqa_db.get_project_by_uuid(project_uuid)
        if not self.app.seaqa_db.is_agent_enabled(project):
            logger.debug('Agent is disabled for project %s, skip event', project_uuid)
            return

        connection_id = data.get('connection_id')
        record_id = data.get('record_id')
        if connection_id is None or record_id is None:
            logger.warning('Ignore event without connection_id or record_id: %s', raw_data)
            return
        try:
            connection_id = int(connection_id)
            record_id = int(record_id)
        except (TypeError, ValueError):
            logger.warning(
                'Ignore event with invalid connection_id=%r or record_id=%r: %s',
                connection_id,
                record_id,
                raw_data,
            )
            return

        if self.route_to_ticket_agent(
            project_uuid=project_uuid,
            source_type=source_type,
            connection_id=connection_id,
            record_id=record_id,
            event=event,
        ):
            return

        self.add_to_task_queue(
            project_uuid=project_uuid,
            source_type=source_type,
            record_id=record_id,
            connection_id=connection_id,
            event=event,
        )

    @staticmethod
    def _is_general_task_finished_event(event: dict) -> bool:
        if event.get('type') != DataEventType.GENERAL_TASK_UPDATED.value:
            return False

        old_value = event.get('old_value')
        if not isinstance(old_value, dict):
            old_value = {}
        new_value = event.get('new_value')
        if not isinstance(new_value, dict):
            new_value = {}

        old_status = str(old_value.get('status') or '').strip().lower()
        new_status = str(new_value.get('status') or '').strip().lower()
        return new_status == 'done' and old_status != 'done'

    def route_to_ticket_agent(
        self,
        project_uuid: str,
        source_type: str,
        connection_id: int,
        record_id: int,
        event: dict,
    ) -> bool:
        """Route a connection-record event to the ticket agent when it is linked.

        Handles both:
        - external linked-record follow-up activity, and
        - internal General Task completion routed into a ticket-centric event.

        Returns True when the event was routed to the ticket agent (the caller
        should stop further handling), False otherwise.
        """
        linked_record_info = self._resolve_record_info(
            project_uuid=project_uuid,
            source_type=source_type,
            connection_id=connection_id,
            record_id=record_id,
        )
        if linked_record_info is None:
            return False

        if source_type == AgentSourceType.GENERAL_TASK.value:
            ticket_id = int(linked_record_info['linked_ticket'])
            if not self._are_all_linked_general_tasks_done(project_uuid, ticket_id):
                logger.info('Skip ticket_task_finished for ticket %s: linked tasks are not all done.', ticket_id)
                return True
            routed_event = self._build_internal_task_finished_event(
                task_connection_id=connection_id,
                task_record_id=record_id,
                task_source_title=linked_record_info.get('title', ''),
                original_event=event,
            )
        else:
            routed_event = self._build_external_linked_record_activity_event(
                original_source_type=source_type,
                original_connection_id=connection_id,
                original_record_id=record_id,
                original_source_title=linked_record_info.get('title', ''),
                original_event=event,
            )

        # Linked-record activity does not use the ticket pending/debounce
        # mechanism: the upstream connection sync already throttles these
        # events, so enqueue the ticket task directly.
        self.add_to_task_queue(
            project_uuid=project_uuid,
            source_type=AgentSourceType.TICKET.value,
            record_id=int(linked_record_info['linked_ticket']),
            connection_id=None,
            event=routed_event,
        )
        return True

    def _are_all_linked_general_tasks_done(
        self,
        project_uuid: str,
        ticket_id: int,
    ) -> bool:
        """Return whether every active General Task linked to the ticket is done."""
        project_uuid_32 = uuid_str_to_32_chars(project_uuid)
        with self.db_session_class() as db_session:
            connections = ProjectConnection.get_connections_by_project_and_type(
                db_session,
                project_uuid_32,
                AgentSourceType.GENERAL_TASK.value,
            )
        if not connections:
            return False

        has_linked_task = False
        for connection in connections:
            connection_id = int(connection['id'])
            table_name = SchemaTables.GENERAL_TASK.table_name(connection_id)
            sql = (
                f"SELECT `status` FROM `{table_name}` "
                f"WHERE `linked_ticket` = {int(ticket_id)} "
                f"AND (`deleted` = False OR `deleted` IS NULL)"
            )
            tasks = self.app.seadb.query_rows(project_uuid, sql).get('results', [])
            if not tasks:
                continue
            has_linked_task = True
            if any(
                str(task.get('status') or '').strip().lower() != 'done'
                for task in tasks
            ):
                return False

        return has_linked_task

    def _is_noop_event(self, source_type: str, event: dict) -> bool:
        """Return True for events that carry no signal worth enqueuing an agent run.

        Currently covers:
        - github_issue_updated / github_issue_closed / github_issue_reopened
        - discourse_topic_updated
        - ticket events except ticket_due_soon / ticket_over_due
        - github_issue_comment_added events whose new_value.author is a
          '*[bot]' account (GitHub App convention).
        """
        event_type = event.get('type')
        if source_type == AgentSourceType.GITHUB_ISSUE.value:
            if event_type in {
                DataEventType.GITHUB_ISSUE_UPDATED.value,
                DataEventType.GITHUB_ISSUE_CLOSED.value,
                DataEventType.GITHUB_ISSUE_REOPENED.value,
            }:
                return True
            if event_type == DataEventType.GITHUB_ISSUE_COMMENT_ADDED.value:
                author = str(((event.get('new_value') or {}).get('author') or '')).strip().lower()
                return author.endswith('[bot]')
            return False
        elif source_type == AgentSourceType.DISCOURSE_FORUM.value:
            return event_type == DataEventType.DISCOURSE_TOPIC_UPDATED.value
        elif source_type == AgentSourceType.GENERAL_TASK.value:
            return not self._is_general_task_finished_event(event)

        return False

    def _resolve_record_info(
        self,
        project_uuid: str,
        source_type: str,
        connection_id: int,
        record_id: int,
    ) -> dict | None:
        """Return the linked ticket id and record title when the record is linked.

        Returns None when the record is not linked to a ticket or cannot be loaded.
        """
        table_getter = self._CONNECTION_TABLE_GETTERS.get(source_type)
        if table_getter is None:
            return None

        table_name = table_getter(connection_id)
        sql = (
            f"SELECT `title`, `linked_ticket` FROM `{table_name}` "
            f"WHERE `_pk` = {record_id} "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"LIMIT 1"
        )
        results = self.app.seadb.query_rows(project_uuid, sql).get('results', [])
        if not results:
            return None

        raw_linked_ticket = results[0].get('linked_ticket')
        if raw_linked_ticket in (None, ''):
            return None
        try:
            linked_ticket = int(raw_linked_ticket)
        except (TypeError, ValueError):
            return None

        return {
            'title': str(results[0].get('title') or ''),
            'linked_ticket': linked_ticket,
        }

    def _build_external_linked_record_activity_event(
        self,
        original_source_type: str,
        original_connection_id: int,
        original_record_id: int,
        original_event: dict,
        original_source_title: str = '',
    ) -> dict:
        original_new_value = (original_event or {}).get('new_value')
        if not isinstance(original_new_value, dict):
            original_new_value = {}

        payload = {
            'type': DataEventType.TICKET_LINKED_RECORD_ACTIVITY.value,
            'original_source_type': original_source_type,
            'original_connection_id': original_connection_id,
            'original_record_id': original_record_id,
            'original_source_title': original_source_title or '',
            'original_event_type': (original_event or {}).get('type'),
            'new_value': original_new_value,
            'old_value': {},
        }
        return payload

    def _build_internal_task_finished_event(
        self,
        task_connection_id: int,
        task_record_id: int,
        original_event: dict,
        task_source_title: str = '',
    ) -> dict:
        original_old_value = (original_event or {}).get('old_value')
        if not isinstance(original_old_value, dict):
            original_old_value = {}
        original_new_value = (original_event or {}).get('new_value')
        if not isinstance(original_new_value, dict):
            original_new_value = {}

        previous_status = str(original_old_value.get('status') or '').strip()
        current_status = str(original_new_value.get('status') or '').strip()

        return {
            'type': DataEventType.TICKET_TASK_FINISHED.value,
            'task_source_type': AgentSourceType.GENERAL_TASK.value,
            'task_connection_id': task_connection_id,
            'task_record_id': task_record_id,
            'task_source_title': task_source_title or '',
            'task_event_type': (original_event or {}).get('type'),
            'task_previous_status': previous_status,
            'task_current_status': current_status,
        }

    def _register_pending_event(
        self,
        project_uuid: str,
        record_id: int,
        event: dict,
        now_time: float,
    ):
        # Ticket-only debounce key.
        key = f'{project_uuid}:ticket:{record_id}'

        state = self.pending_events.get(key)
        if state is None:
            state = PendingEventState(
                project_uuid=project_uuid,
                record_id=record_id,
                first_event_time=now_time,
            )
            self.pending_events[key] = state

        state.events.append(event.copy())

        if self._can_enqueue(state, now_time):
            self._enqueue_from_state(state, now_time)

    def _flush_pending_records(self, now_time: float):
        keys_to_remove = []
        for key, state in self.pending_events.items():
            if self._can_enqueue(state, now_time):
                self._enqueue_from_state(state, now_time)
            if (state.last_enqueue_time > 0
                    and not state.events
                    and (now_time - state.last_enqueue_time) >= config.AGENT_ENQUEUE_DEBOUNCE_INTERVAL):
                keys_to_remove.append(key)
        for key in keys_to_remove:
            self.pending_events.pop(key)

    def _can_enqueue(self, state: PendingEventState, now_time: float) -> bool:
        # Only enqueue when there is at least one new event since the last enqueue and the debounce interval has passed
        if not state.events:
            return False

        # for first-time enqueue request
        if state.last_enqueue_time == 0:
            # ticket pending requires a specific initial debounce period.
            duration_after_created = now_time - state.first_event_time
            return duration_after_created >= config.AGENT_ENQUEUE_TICKET_INITIAL_DEBOUNCE

        # for non-first-time enqueue request, check debounce interval
        return (now_time - state.last_enqueue_time) >= config.AGENT_ENQUEUE_DEBOUNCE_INTERVAL

    def _enqueue_from_state(self, state: PendingEventState, now_time: float):
        compacted_events = compact_events(state.events)
        for event in compacted_events:
            self.add_to_task_queue(
                project_uuid=state.project_uuid,
                source_type=AgentSourceType.TICKET.value,
                record_id=state.record_id,
                connection_id=None,
                event=event,
            )
        state.events.clear()
        state.last_enqueue_time = now_time

    def _due_scanner(self):
        while not self.should_stop.is_set():
            try:
                self._enqueue_due_scan_events()
            except Exception as e:
                logger.exception('Due-soon scan failed: %s', e)
            if self.should_stop.wait(config.AGENT_TIMED_SCAN_INTERVAL):
                return

    def _enqueue_due_scan_events(self):
        projects = self.app.seaqa_db.get_enabled_agent_projects()
        now = datetime.now(timezone.utc)
        for project in projects:
            project_uuid = project.get('uuid')
            if not project_uuid:
                continue

            last_scanned_at = project.get('last_agent_scanned_at')
            if last_scanned_at is not None:
                if last_scanned_at.tzinfo is None:
                    last_scanned_at = last_scanned_at.replace(tzinfo=timezone.utc)
                if (now - last_scanned_at) < timedelta(seconds=config.AGENT_TIMED_SCAN_PROJECT_COOLDOWN):
                    logger.debug(
                        'Skipping due-soon scan for project %s (last scanned %s ago)',
                        project_uuid, now - last_scanned_at,
                    )
                    continue

            try:
                due_soon_tickets = self._get_due_soon_tickets(project_uuid, project, now)
                over_due_tickets = self._get_over_due_tickets(project_uuid, now)
            except Exception as e:
                logger.exception('Failed to get timed tickets for project %s: %s', project_uuid, e)
                continue

            self._enqueue_timed_ticket_events(
                project_uuid,
                due_soon_tickets,
                DataEventType.TICKET_DUE_SOON.value,
            )
            self._enqueue_timed_ticket_events(
                project_uuid,
                over_due_tickets,
                DataEventType.TICKET_OVER_DUE.value,
            )
            self.app.seaqa_db.update_project_agent_scanned_at(project_uuid)

    def _enqueue_timed_ticket_events(self, project_uuid: str, tickets: list[dict], event_type: str):
        for ticket in tickets:
            ticket_pk = ticket.get('_pk')
            if ticket_pk is None:
                continue
            self.add_to_task_queue(
                project_uuid=project_uuid,
                source_type=AgentSourceType.TICKET.value,
                record_id=ticket_pk,
                connection_id=None,
                event={
                    'type': event_type,
                    'new_value': {},
                    'old_value': {},
                },
            )

    def _get_due_soon_tickets(self, project_uuid: str, project: dict, now: datetime) -> list[dict]:
        """Query tickets that are due soon and have not been recently processed."""
        agent_settings = project.get('settings', {}).get('agent', {})
        notify_before_due_hours = int(agent_settings.get('notify_before_due_hours'))
        min_reprocess_hours = 24

        now_iso = now.isoformat()
        threshold = (now + timedelta(hours=notify_before_due_hours)).isoformat()
        min_reprocess_before = (now - timedelta(hours=min_reprocess_hours)).isoformat()

        tickets_table = SchemaTables.TICKETS.table_name()
        sql = (
            f"SELECT `_pk` FROM `{tickets_table}` "
            f"WHERE `due_date` IS NOT NULL AND `due_date` >= '{now_iso}' AND `due_date` < '{threshold}' "
            f"AND (`state` != 'closed' OR `state` IS NULL) "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"AND (`last_agent_processed_at` IS NULL OR `last_agent_processed_at` < '{min_reprocess_before}') "
            f"ORDER BY `due_date` ASC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self.app.seadb.query_rows(project_uuid, sql).get('results', [])

    def _get_over_due_tickets(self, project_uuid: str, now: datetime) -> list[dict]:
        """Query tickets that are over due and have not been recently processed."""
        min_reprocess_hours = 24
        now_iso = now.isoformat()
        min_reprocess_before = (now - timedelta(hours=min_reprocess_hours)).isoformat()

        tickets_table = SchemaTables.TICKETS.table_name()
        sql = (
            f"SELECT `_pk` FROM `{tickets_table}` "
            f"WHERE `due_date` IS NOT NULL AND `due_date` < '{now_iso}' "
            f"AND (`state` != 'closed' OR `state` IS NULL) "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"AND (`last_agent_processed_at` IS NULL OR `last_agent_processed_at` < '{min_reprocess_before}') "
            f"ORDER BY `due_date` ASC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self.app.seadb.query_rows(project_uuid, sql).get('results', [])
