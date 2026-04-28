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
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.utils.constants import TICKET_TABLES
from seaqa_ai.agent.utils.constants import (
    AGENT_TASK_QUEUE_NAME,
    DATA_EVENTS_CHANNEL,
    DataEventType,
    SourceType,
)

logger = logging.getLogger(__name__)


@dataclass
class PendingTaskState:
    project_uuid: str
    source_type: str
    record_id: int
    connection_id: int | None
    events: list = field(default_factory=list)
    last_enqueue_time: float = 0.0
    not_before: float = 0.0  # Earliest time this record can be enqueued (for ticket delay)


class AgentManager:
    """Listen to events and enqueue agent tasks."""

    def __init__(self, app):
        self.app = app
        self.mq = app.redis_client.connection
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self.should_stop = threading.Event()
        self.worker_list = []
        self.pending_tasks: dict[str, PendingTaskState] = {}
        self._check_pending_events_interval = 5
        self._retry_interval = 3
        self._no_message_timeout = 5 * 60 # 5 minutes
        self._debounce_interval = 10 * 60 # 10 minutes
        self._due_soon_scan_interval = 1 * 60 * 60 # 1 hour
        self._min_scan_interval_hours = 24 # skip project if scanned within last 24 hours
        self._ticket_delay_seconds = 5 * 60 # 5 minutes delay for ticket events before first enqueue

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

        due_soon_scanner = threading.Thread(
            target=self._due_soon_scanner,
            name='agent_due_soon_scanner',
            daemon=True,
        )
        due_soon_scanner.start()
        self.worker_list.append(due_soon_scanner)

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
        events: list | None,
    ) -> dict:
        """Add one task message into the agent task queue."""
        project_uuid = uuid_str_to_32_chars(project_uuid)
        if not project_uuid:
            raise ValueError('project_uuid is required')

        message = {
            'project_uuid': project_uuid,
            'source_type': source_type,
            'record_id': record_id,
            'events': list(events) if events else [],
        }
        if connection_id is not None:
            message['connection_id'] = connection_id

        self.mq.lpush(AGENT_TASK_QUEUE_NAME, json.dumps(message))
        logger.info(
            'Added agent task to queue for project %s (source=%s record_id=%s connection_id=%s events=%s)',
            project_uuid,
            source_type,
            record_id,
            connection_id,
            message['events'],
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

                if (now_time - last_pending_check_time) >= self._check_pending_events_interval:
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
        if not SourceType.is_valid(source_type):
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

        record_id = data.get('record_id')
        if record_id is None:
            logger.warning('Ignore event without record_id: %s', raw_data)
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
        self._register_pending_record(
            project_uuid=project_uuid,
            source_type=source_type,
            record_id=record_id,
            connection_id=connection_id,
            event=event,
            now_time=now_time,
        )

    def _is_noop_event(self, source_type: str, event: dict) -> bool:
        """Return True for events that carry no signal worth enqueuing an agent run.

        Currently covers github_issue_comment_added events whose new_value.comments
        are all authored by '*[bot]' accounts (GitHub App convention).

        Events that mix bot and human authors in a single new_value.comments list
        are NOT filtered here; they are forwarded so that GithubIssueAgent can
        apply per-comment filtering downstream.
        """
        if source_type != SourceType.GITHUB_ISSUE.value:
            return False
        if event.get('type') != DataEventType.GITHUB_ISSUE_COMMENT_ADDED.value:
            return False
        comments = ((event.get('new_value') or {}).get('comments') or [])
        if not comments:
            return False
        for c in comments:
            if not isinstance(c, dict):
                return False
            author = str(c.get('author') or '').strip().lower()
            if not author.endswith('[bot]'):
                return False
        return True

    def _register_pending_record(
        self,
        project_uuid: str,
        source_type: str,
        record_id: int,
        connection_id: int | None,
        event: dict,
        now_time: float,
    ):
        # Debounce key is per-record: {project_uuid}:{connection_id}:{record_id}
        # For ticket (no connection_id): {project_uuid}:ticket:{record_id}
        if connection_id is not None:
            key = f'{project_uuid}:{connection_id}:{record_id}'
        else:
            key = f'{project_uuid}:ticket:{record_id}'

        state = self.pending_tasks.get(key)
        if state is None:
            # For tickets, delay first enqueue to allow absorbing consecutive edits (e.g., modify then close)
            not_before = now_time + self._ticket_delay_seconds if source_type == SourceType.TICKET.value else 0.0
            state = PendingTaskState(
                project_uuid=project_uuid,
                source_type=source_type,
                record_id=record_id,
                connection_id=connection_id,
                not_before=not_before,
            )
            self.pending_tasks[key] = state

        state.events.append(event.copy())

        if self._can_enqueue(state, now_time):
            self._enqueue_from_state(state, now_time)

    def _flush_pending_records(self, now_time: float):
        keys_to_remove = []
        for key, state in self.pending_tasks.items():
            if self._can_enqueue(state, now_time):
                self._enqueue_from_state(state, now_time)
            if (state.last_enqueue_time > 0
                    and not state.events
                    and (now_time - state.last_enqueue_time) >= self._debounce_interval):
                keys_to_remove.append(key)
        for key in keys_to_remove:
            self.pending_tasks.pop(key)

    def _can_enqueue(self, state: PendingTaskState, now_time: float) -> bool:
        # Only enqueue when there is at least one new event since the last enqueue and the debounce interval has passed
        if not state.events:
            return False
        if now_time < state.not_before:
            return False
        return (now_time - state.last_enqueue_time) >= self._debounce_interval

    def _enqueue_from_state(self, state: PendingTaskState, now_time: float):
        self.add_to_task_queue(
            project_uuid=state.project_uuid,
            source_type=state.source_type,
            record_id=state.record_id,
            connection_id=state.connection_id,
            events=state.events,
        )
        state.events.clear()
        state.last_enqueue_time = now_time

    def _due_soon_scanner(self):
        while not self.should_stop.is_set():
            try:
                self._enqueue_due_soon_scan_tasks()
            except Exception as e:
                logger.exception('Due-soon scan failed: %s', e)
            if self.should_stop.wait(self._due_soon_scan_interval):
                return

    def _enqueue_due_soon_scan_tasks(self):
        projects = self.app.seaqa_db.get_enabled_projects()
        now = datetime.now(timezone.utc)
        for project in projects:
            project_uuid = project.get('uuid')
            if not project_uuid:
                continue

            last_scanned_at = project.get('last_agent_scanned_at')
            if last_scanned_at is not None:
                if last_scanned_at.tzinfo is None:
                    last_scanned_at = last_scanned_at.replace(tzinfo=timezone.utc)
                if (now - last_scanned_at) < timedelta(hours=self._min_scan_interval_hours):
                    logger.debug(
                        'Skipping due-soon scan for project %s (last scanned %s ago)',
                        project_uuid, now - last_scanned_at,
                    )
                    continue

            try:
                tickets = self._get_due_soon_tickets(project_uuid, project)
            except Exception as e:
                logger.exception('Failed to get due-soon tickets for project %s: %s', project_uuid, e)
                continue
            for ticket in tickets:
                ticket_pk = ticket.get('_pk')
                if ticket_pk is None:
                    continue
                self.add_to_task_queue(
                    project_uuid=project_uuid,
                    source_type=SourceType.TICKET.value,
                    record_id=ticket_pk,
                    connection_id=None,
                    events=[{
                        'type': DataEventType.TICKET_DUE_SOON.value,
                        'new_value': {},
                        'old_value': {},
                    }],
                )
            self.app.seaqa_db.update_project_agent_scanned_at(project_uuid)

    def _get_due_soon_tickets(self, project_uuid: str, project: dict) -> list[dict]:
        """Query tickets that are due soon and have not been recently processed."""
        agent_settings = project.get('settings', {}).get('agent', {})
        notify_before_due_hours = int(agent_settings.get('notify_before_due_hours', 48))
        min_reprocess_hours = 24

        now = datetime.now(timezone.utc)
        threshold = (now + timedelta(hours=notify_before_due_hours)).isoformat()
        min_reprocess_before = (now - timedelta(hours=min_reprocess_hours)).isoformat()

        tickets_table = TICKET_TABLES.TICKETS_TABLE.value
        sql = (
            f"SELECT * FROM `{tickets_table}` "
            f"WHERE `due_date` IS NOT NULL AND `due_date` < '{threshold}' "
            f"AND (`state` != 'closed' OR `state` IS NULL) "
            f"AND (`deleted` = False OR `deleted` IS NULL) "
            f"AND (`last_agent_processed_at` IS NULL OR `last_agent_processed_at` < '{min_reprocess_before}') "
            f"ORDER BY `due_date` ASC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self.app.seadb_api.query_rows(project_uuid, sql).get('results', [])
