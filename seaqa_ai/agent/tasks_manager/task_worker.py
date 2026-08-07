"""Redis consumer for typed agent tasks."""
import logging
import threading

from redis.exceptions import ConnectionError as NoMQAvailable, ResponseError, TimeoutError

from seaqa_ai.agent.agent_service import AgentService

from .constants import AGENT_TASK_QUEUE_NAME, AGENT_TASK_SCHEMA_VERSION
from .serializer import AgentTaskSerializer


logger = logging.getLogger(__name__)


class AgentTaskWorker:
    """Consume current-version task messages and execute their AgentService run."""

    def __init__(self, app, worker_num=3):
        if not isinstance(worker_num, int) or isinstance(worker_num, bool) or worker_num < 1:
            raise ValueError('worker_num must be a positive integer')
        self._app = app
        self._redis_connection = app.redis_client.connection
        self._worker_num = worker_num
        self._should_stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if not self._redis_connection:
            logger.error('Redis is unavailable; agent task worker will not start.')
            return
        if any(thread.is_alive() for thread in self._threads):
            return

        self._should_stop.clear()
        self._threads = [
            threading.Thread(
                target=self._consume_loop,
                name=f'agent_task_worker_{index}',
                daemon=True,
            )
            for index in range(self._worker_num)
        ]
        for thread in self._threads:
            thread.start()
        logger.info('Agent task worker started with %s threads', self._worker_num)

    def stop(self) -> None:
        self._should_stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads = []
        logger.info('Agent task worker stopped')

    def _consume_loop(self) -> None:
        while not self._should_stop.is_set():
            try:
                message = self._redis_connection.brpop(AGENT_TASK_QUEUE_NAME, timeout=30)
                if message is None:
                    continue
                _, payload = message
                self._process_payload(payload)
            except (ResponseError, NoMQAvailable, TimeoutError) as error:
                logger.error('Redis failure in agent task worker: %s', error)
                self._should_stop.wait(1)
            except Exception:
                logger.exception('Unexpected agent task worker failure')
                self._should_stop.wait(1)

    def _process_payload(self, payload) -> None:
        try:
            task = AgentTaskSerializer.deserialize(payload)
        except (UnicodeDecodeError, ValueError, TypeError, KeyError):
            logger.warning('Dropping malformed agent task payload', exc_info=True)
            return

        if task.schema_version != AGENT_TASK_SCHEMA_VERSION:
            logger.warning(
                'Dropping unsupported agent task schema: task_id=%s version=%s',
                task.task_id,
                task.schema_version,
            )
            return

        project = self._app.seaqa_db.get_project_by_uuid(task.project_uuid)
        if not project:
            logger.warning(
                'Dropping agent task for missing project: task_id=%s project=%s',
                task.task_id,
                task.project_uuid,
            )
            return
        if not self._app.seaqa_db.is_agent_enabled(project):
            logger.info(
                'Skipping agent task because the agent is disabled: task_id=%s project=%s',
                task.task_id,
                task.project_uuid,
            )
            return

        try:
            AgentService(self._app, project).run(task)
        except Exception:
            logger.exception(
                'Agent task failed: task_id=%s project=%s target=%s:%s',
                task.task_id,
                task.project_uuid,
                task.target_source_type,
                task.target_record_id,
            )
