import logging
import time
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

from seaqa_ai import config
from seaqa_ai.agent.models import AgentEvent, AgentTask
from seaqa_ai.agent.utils import compact_events

from .constants import (
    AGENT_TASK_QUEUE_NAME,
    AGENT_TASK_SCHEMA_VERSION,
    PENDING_TASK_STATE_TTL_SECONDS,
    SHOULD_DEBOUNCE_EVENTS_TYPE,
)
from .serializer import AgentTaskSerializer

logger = logging.getLogger(__name__)


class TaskQueueManager:
    """Persist agent tasks in Redis and debounce selected events in memory."""

    def __init__(self, redis_connection):
        self._redis_connection = redis_connection
        self._pending_task_states: dict[tuple, dict] = {}
        self._pending_task_lock = RLock()

    def submit(self, task: AgentTask) -> bool:
        if task.trigger_event.event_type not in SHOULD_DEBOUNCE_EVENTS_TYPE:
            return self._enqueue(task)
        return self._append_pending_task(task)

    def flush_due(self) -> int:
        """Flush due pending task aggregates and return the number of emitted tasks."""
        now = time.time()
        with self._pending_task_lock:
            keys = []
            for pending_task_key, state in list(self._pending_task_states.items()):
                if not state['pending_tasks']:
                    if state['expires_at'] <= now:
                        del self._pending_task_states[pending_task_key]
                    continue
                if state['due_at'] <= now:
                    keys.append(pending_task_key)

        emitted = 0
        for pending_task_key in keys:
            emitted += self._flush_pending_task(pending_task_key, now)
        return emitted

    def _append_pending_task(self, task: AgentTask) -> bool:
        pending_task_key = self._pending_task_key(task)
        now = time.time()
        with self._pending_task_lock:
            state = self._pending_task_states.get(pending_task_key)
            if state is None:
                state = {
                    'first_event_at': now,
                    'last_enqueued_at': 0.0,
                    'due_at': None,
                    'expires_at': now + PENDING_TASK_STATE_TTL_SECONDS,
                    'pending_tasks': [],
                }
                self._pending_task_states[pending_task_key] = state
            state['pending_tasks'].append(task)
            state['due_at'] = self._next_due_at(state, now)
            state['expires_at'] = now + PENDING_TASK_STATE_TTL_SECONDS
        return True

    def _flush_pending_task(self, pending_task_key: tuple, now: float) -> int:
        with self._pending_task_lock:
            state = self._pending_task_states.get(pending_task_key)
            if state is None or not state['pending_tasks'] or state['due_at'] > now:
                return 0

            pending_tasks = state['pending_tasks']
            compacted_events = compact_events([task.trigger_event.event for task in pending_tasks])
            emitted_tasks = [
                self._task_with_event(pending_tasks[0], event)
                for event in compacted_events
            ]
            if not all(self._enqueue(task) for task in emitted_tasks):
                state['due_at'] = now + config.AGENT_ENQUEUE_PENDING_EVENTS_CHECK_INTETVAL
                return 0

            state['pending_tasks'] = []
            state['due_at'] = None
            state['last_enqueued_at'] = now
            state['first_event_at'] = now
            state['expires_at'] = now + PENDING_TASK_STATE_TTL_SECONDS
            return len(emitted_tasks)

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
        )
        return AgentTask(
            schema_version=AGENT_TASK_SCHEMA_VERSION,
            task_id=str(uuid4()),
            project_uuid=template.project_uuid,
            target_source_type=template.target_source_type,
            target_record_id=template.target_record_id,
            target_connection_id=template.target_connection_id,
            trigger_event=trigger_event,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _next_due_at(state: dict, now: float) -> float:
        if state.get('last_enqueued_at', 0.0):
            return float(state['last_enqueued_at']) + config.AGENT_ENQUEUE_DEBOUNCE_INTERVAL
        return float(state.get('first_event_at', now)) + config.AGENT_ENQUEUE_TICKET_INITIAL_DEBOUNCE

    def _pending_task_key(self, task: AgentTask) -> tuple:
        return (
            task.project_uuid,
            task.target_source_type,
            task.target_connection_id,
            str(task.target_record_id),
        )
