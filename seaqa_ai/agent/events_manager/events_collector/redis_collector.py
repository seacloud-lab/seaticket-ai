import json
import logging
import threading
import time
from collections.abc import Callable

from redis.exceptions import ConnectionError as NoMQAvailable, ResponseError, TimeoutError

from ..constants import (
    DATA_EVENTS_CHANNEL,
    REDIS_NO_MESSAGE_TIMEOUT_SECONDS,
    REDIS_RETRY_INTERVAL_SECONDS,
)
from seaqa_ai.agent.models import AgentEvent
from ..validator import EventValidationError, EventsValidator

logger = logging.getLogger(__name__)


class RedisEventsCollector:
    """Collect and normalize trigger events published to Redis."""

    def __init__(self, redis_connection, validator: EventsValidator | None = None):
        self._redis_connection = redis_connection
        self._validator = validator or EventsValidator()
        self._should_stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_event: Callable[[AgentEvent], bool] | None = None

    def start(self, on_event: Callable[[AgentEvent], bool]) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._should_stop.clear()
        self._on_event = on_event
        self._thread = threading.Thread(
            target=self._listen,
            name='agent_redis_events_collector',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._should_stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _listen(self) -> None:
        while not self._should_stop.is_set():
            try:
                self._listen_once()
            except (ResponseError, NoMQAvailable, TimeoutError) as error:
                logger.error('Redis event listener failure: %s', error)
            except Exception:
                logger.exception('Redis event listener failed')
            if self._should_stop.wait(REDIS_RETRY_INTERVAL_SECONDS):
                return

    def _listen_once(self) -> None:
        with self._redis_connection.pubsub(ignore_subscribe_messages=True) as pubsub:
            pubsub.subscribe(DATA_EVENTS_CHANNEL)
            logger.info('Subscribed to agent event channel: %s', DATA_EVENTS_CHANNEL)
            last_message_time = time.monotonic()
            while not self._should_stop.is_set():
                message = pubsub.get_message(timeout=1.0)
                now = time.monotonic()
                if message is not None:
                    last_message_time = now
                    self._handle_message(message)
                elif now - last_message_time >= REDIS_NO_MESSAGE_TIMEOUT_SECONDS:
                    logger.warning('No events for %ss; reconnecting.', REDIS_NO_MESSAGE_TIMEOUT_SECONDS)
                    return

    def _handle_message(self, message: dict) -> None:
        raw_data = message.get('data')
        if not isinstance(raw_data, str):
            return
        try:
            data = json.loads(raw_data)
            event = self._validator.validate(data)
        except (json.JSONDecodeError, EventValidationError) as error:
            logger.warning('Ignoring invalid agent event: %s', error)
            return

        try:
            if self._on_event:
                self._on_event(event)
        except Exception:
            logger.exception('Agent event callback failed')
