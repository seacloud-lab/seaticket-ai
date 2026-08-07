import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from seaqa_ai import config
from seaqa_ai.agent.models import AgentEvent, AgentTask
from seaqa_ai.agent.utils import compact_events

from .constants import (
    AGENT_TASK_QUEUE_NAME,
    AGENT_TASK_SCHEMA_VERSION,
    PENDING_TICKET_DUE_KEY,
    PENDING_TICKET_KEY_PREFIX,
    PENDING_TICKET_LOCK_PREFIX,
    PENDING_TICKET_LOCK_TTL_SECONDS,
    PENDING_TICKET_STATE_TTL_SECONDS,
)
from .dispatch_policy import TaskDispatchPolicy
from .serializer import AgentTaskSerializer

logger = logging.getLogger(__name__)


class TaskQueueManager:
    """Persist agent tasks and coordinate Ticket event debounce in Redis."""

    def __init__(self, redis_connection, dispatch_policy: TaskDispatchPolicy | None = None):
        self._redis_connection = redis_connection
        self._dispatch_policy = dispatch_policy or TaskDispatchPolicy()

    def submit(self, task: AgentTask) -> bool:
        if not self._dispatch_policy.should_debounce(task):
            return self._enqueue(task)
        return self._append_pending_ticket_event(task)

    def flush_due(self) -> int:
        """Flush due Ticket aggregates and return the number of emitted tasks."""
        now = time.time()
        try:
            keys = self._redis_connection.zrangebyscore(PENDING_TICKET_DUE_KEY, '-inf', now)
        except Exception:
            logger.exception('Failed to read due Ticket tasks')
            return 0

        emitted = 0
        for pending_key in keys:
            if isinstance(pending_key, bytes):
                pending_key = pending_key.decode('utf-8')
            emitted += self._flush_pending_ticket(str(pending_key), now)
        return emitted

    def _append_pending_ticket_event(self, task: AgentTask) -> bool:
        pending_key = self._pending_key(task)
        token = self._acquire_lock(pending_key)
        if token is None:
            logger.warning('Ticket task is busy, retrying on the next event: %s', pending_key)
            return False

        try:
            state = self._load_state(pending_key)
            now = time.time()
            if state is None:
                state = {
                    'project_uuid': task.project_uuid,
                    'target_source_type': task.target_source_type,
                    'target_record_id': task.target_record_id,
                    'target_connection_id': task.target_connection_id,
                    'first_event_at': now,
                    'last_enqueued_at': 0.0,
                    'events': [],
                }
            state['events'].append(AgentTaskSerializer.to_dict(task))
            due_at = self._next_due_at(state, now)
            self._save_state(pending_key, state)
            self._redis_connection.zadd(PENDING_TICKET_DUE_KEY, {pending_key: due_at})
            return True
        except Exception:
            logger.exception('Failed to persist pending Ticket task: %s', pending_key)
            return False
        finally:
            self._release_lock(pending_key, token)

    def _flush_pending_ticket(self, pending_key: str, now: float) -> int:
        token = self._acquire_lock(pending_key)
        if token is None:
            return 0
        try:
            state = self._load_state(pending_key)
            if state is None:
                self._redis_connection.zrem(PENDING_TICKET_DUE_KEY, pending_key)
                return 0
            due_at = self._next_due_at(state, now)
            if due_at > now:
                self._redis_connection.zadd(PENDING_TICKET_DUE_KEY, {pending_key: due_at})
                return 0

            tasks = [AgentTaskSerializer.deserialize(json.dumps(item)) for item in state['events']]
            compacted_events = compact_events([task.trigger_event.event for task in tasks])
            emitted_tasks = [
                self._task_with_event(tasks[0], event)
                for event in compacted_events
            ]
            if not all(self._enqueue(task) for task in emitted_tasks):
                self._redis_connection.zadd(
                    PENDING_TICKET_DUE_KEY,
                    {pending_key: now + config.AGENT_ENQUEUE_PENDING_EVENTS_CHECK_INTETVAL},
                )
                return 0

            state['events'] = []
            state['last_enqueued_at'] = now
            state['first_event_at'] = now
            self._save_state(pending_key, state)
            self._redis_connection.zrem(PENDING_TICKET_DUE_KEY, pending_key)
            return len(emitted_tasks)
        except Exception:
            logger.exception('Failed to flush pending Ticket task: %s', pending_key)
            try:
                self._redis_connection.zadd(
                    PENDING_TICKET_DUE_KEY,
                    {pending_key: now + config.AGENT_ENQUEUE_PENDING_EVENTS_CHECK_INTETVAL},
                )
            except Exception:
                logger.exception('Failed to reschedule pending Ticket task: %s', pending_key)
            return 0
        finally:
            self._release_lock(pending_key, token)

    def _enqueue(self, task: AgentTask) -> bool:
        try:
            self._redis_connection.lpush(AGENT_TASK_QUEUE_NAME, AgentTaskSerializer.serialize(task))
            logger.debug(
                'Queued agent task %s for project %s (target=%s:%s)',
                task.task_id,
                task.project_uuid,
                task.target_source_type,
                task.target_record_id,
            )
            return True
        except Exception:
            logger.exception('Failed to queue agent task %s', task.task_id)
            return False

    @staticmethod
    def _task_with_event(template: AgentTask, event: dict) -> AgentTask:
        trigger = template.trigger_event
        trigger_event = AgentEvent(
            project_uuid=trigger.project_uuid,
            source_type=trigger.source_type,
            record_id=trigger.record_id,
            event_type=event['type'],
            event={
                'type': event['type'],
                'old_value': event.get('old_value') or {},
                'new_value': event.get('new_value') or {},
            },
            connection_id=trigger.connection_id,
            modified_time=trigger.modified_time,
            received_at=trigger.received_at,
            event_id=trigger.event_id,
            correlation_id=trigger.correlation_id,
            causation_id=trigger.causation_id,
        )
        return AgentTask(
            schema_version=AGENT_TASK_SCHEMA_VERSION,
            task_id=str(uuid4()),
            idempotency_key=TaskQueueManager._build_idempotency_key(template, event),
            project_uuid=template.project_uuid,
            target_source_type=template.target_source_type,
            target_record_id=template.target_record_id,
            target_connection_id=template.target_connection_id,
            trigger_event=trigger_event,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _build_idempotency_key(template: AgentTask, event: dict) -> str:
        payload = {
            'project_uuid': template.project_uuid,
            'source_type': template.target_source_type,
            'connection_id': template.target_connection_id,
            'record_id': template.target_record_id,
            'event': event,
            'modified_time': (
                template.trigger_event.modified_time.isoformat()
                if template.trigger_event.modified_time else None
            ),
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

    @staticmethod
    def _next_due_at(state: dict, now: float) -> float:
        if state.get('last_enqueued_at', 0.0):
            return float(state['last_enqueued_at']) + config.AGENT_ENQUEUE_DEBOUNCE_INTERVAL
        return float(state.get('first_event_at', now)) + config.AGENT_ENQUEUE_TICKET_INITIAL_DEBOUNCE

    def _pending_key(self, task: AgentTask) -> str:
        return ':'.join((
            PENDING_TICKET_KEY_PREFIX,
            task.project_uuid,
            str(task.target_record_id),
        ))

    def _load_state(self, pending_key: str) -> dict | None:
        value = self._redis_connection.get(pending_key)
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        return json.loads(value)

    def _save_state(self, pending_key: str, state: dict) -> None:
        self._redis_connection.set(
            pending_key,
            json.dumps(state, ensure_ascii=False, separators=(',', ':')),
            ex=PENDING_TICKET_STATE_TTL_SECONDS,
        )

    def _acquire_lock(self, pending_key: str) -> str | None:
        token = str(uuid4())
        lock_key = f'{PENDING_TICKET_LOCK_PREFIX}:{pending_key}'
        if self._redis_connection.set(lock_key, token, nx=True, ex=PENDING_TICKET_LOCK_TTL_SECONDS):
            return token
        return None

    def _release_lock(self, pending_key: str, token: str | None) -> None:
        if token is None:
            return
        lock_key = f'{PENDING_TICKET_LOCK_PREFIX}:{pending_key}'
        try:
            # Only the owner may release a lock that could have expired and been reacquired.
            lock_value = self._redis_connection.get(lock_key)
            if isinstance(lock_value, bytes):
                lock_value = lock_value.decode('utf-8')
            if lock_value == token:
                self._redis_connection.delete(lock_key)
        except Exception:
            logger.warning('Failed to release Ticket task lock: %s', pending_key)
