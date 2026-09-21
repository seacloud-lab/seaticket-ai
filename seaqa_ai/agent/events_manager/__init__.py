from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from seaqa_ai import config
from seaqa_ai.agent.models import AgentTask
from seaqa_ai.agent.tasks_manager.constants import AGENT_TASK_SCHEMA_VERSION

from .constants import EVENT_TYPE_GITHUB_ISSUE_COMMENT_ADDED, TASK_COMPLETION_EVENT_SPECS

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from seaqa_ai.agent.models import AgentEvent


class EventsManager:
    """Coordinate event sources and submit accepted events to the task queue."""

    def __init__(self, app):
        from .events_collector.redis_collector import RedisEventsCollector
        from .events_collector.timed_collector import TimedEventsCollector

        self._task_manager = app.task_manager
        self._redis_collector = RedisEventsCollector(app.redis_client.connection)
        self._timed_collector = TimedEventsCollector(app)
        self._should_stop = threading.Event()
        self._flush_thread: threading.Thread | None = None

    def start(self) -> None:
        self._should_stop.clear()
        self._start_flush_loop()
        self._redis_collector.start(self.handle)
        self._timed_collector.start(self.handle)
        logger.info('Events manager started')

    def stop(self) -> None:
        self._redis_collector.stop()
        self._timed_collector.stop()
        self._should_stop.set()
        if self._flush_thread:
            self._flush_thread.join(timeout=2)
        logger.info('Events manager stopped')

    def handle(self, event: AgentEvent) -> bool:
        if not self._should_process(event):
            logger.debug(
                'Ignoring no-op agent event (source=%s type=%s record=%s)',
                event.source_type,
                event.event_type,
                event.record_id,
            )
            return True
        return self._task_manager.submit(
            AgentTask(
                schema_version=AGENT_TASK_SCHEMA_VERSION,
                task_id=str(uuid4()),
                project_uuid=event.project_uuid,
                target_item_type=event.source_type,
                target_record_id=event.record_id,
                target_connection_id=event.connection_id,
                trigger_event=event,
                created_at=datetime.now(timezone.utc),
            )
        )

    def _start_flush_loop(self) -> None:
        if self._flush_thread and self._flush_thread.is_alive():
            return
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name='agent_task_queue_flush',
            daemon=True,
        )
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        while not self._should_stop.is_set():
            try:
                self._task_manager.flush_due()
            except Exception:
                logger.exception('Failed to flush pending agent tasks')
            if self._should_stop.wait(config.AGENT_ENQUEUE_PENDING_EVENTS_CHECK_INTETVAL):
                return

    @staticmethod
    def _should_process(event: AgentEvent) -> bool:
        completion_spec = TASK_COMPLETION_EVENT_SPECS.get(event.event_type)
        if completion_spec:
            field, done_value = completion_spec
            old_value = str(event.event['old_value'].get(field) or '').strip().lower()
            new_value = str(event.event['new_value'].get(field) or '').strip().lower()
            return old_value != done_value and new_value == done_value
        if event.source_type == 'github_issue' and event.event_type == EVENT_TYPE_GITHUB_ISSUE_COMMENT_ADDED:
            author = str(event.event['new_value'].get('author') or '').strip().lower()
            return not author.endswith('[bot]')
        return True
