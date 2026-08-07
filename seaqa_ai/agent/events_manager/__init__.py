from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from seaqa_ai import config

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from seaqa_ai.agent.models import AgentEvent
    from seaqa_ai.agent.tasks_manager.queue_manager import TaskQueueManager


class EventsManager:
    """Coordinate event sources and submit accepted events to the task queue."""

    def __init__(self, app, task_queue_manager: TaskQueueManager):
        from .events_collector.redis_collector import RedisEventsCollector
        from .events_collector.timed_collector import TimedEventsCollector
        from .router import EventRouter
        from .validator import EventsFilter

        self._task_queue_manager = task_queue_manager
        self._events_filter = EventsFilter()
        self._router = EventRouter()
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
        if not self._events_filter.should_process(event):
            logger.debug(
                'Ignoring no-op agent event (source=%s type=%s record=%s)',
                event.source_type,
                event.event_type,
                event.record_id,
            )
            return True
        return self._task_queue_manager.submit(self._router.route(event))

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
                self._task_queue_manager.flush_due()
            except Exception:
                logger.exception('Failed to flush pending agent tasks')
            if self._should_stop.wait(config.AGENT_ENQUEUE_PENDING_EVENTS_CHECK_INTETVAL):
                return
