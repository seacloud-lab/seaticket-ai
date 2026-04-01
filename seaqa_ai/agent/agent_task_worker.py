import json
import logging
import threading
import time

from redis.exceptions import ConnectionError as NoMQAvailable, ResponseError, TimeoutError

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.agent.ticket_agent import TicketAgent
from seaqa_ai.agent.github_agent import GithubIssueAgent
from seaqa_ai.agent.discourse_agent import DiscourseAgent
from seaqa_ai.agent.utils.constants import (
    AGENT_TASK_QUEUE_NAME,
    SourceType,
)
from seaqa_ai.utils import uuid_str_to_32_chars

logger = logging.getLogger(__name__)


class AgentTaskWorker:
    """Queue consumer that executes agent tasks at single-record granularity.

    Each task processes exactly one issue or ticket, so no distributed locking
    is needed — concurrent workers can safely handle different records in parallel.
    """

    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self.mq = app.redis_client.connection
        self.should_stop = threading.Event()
        self.worker_list = []

    @property
    def worker_num(self) -> int:
        return 3

    def start(self):
        if not self.mq:
            logger.error('Redis is unavailable, agent worker will not start.')
            return

        for i in range(self.worker_num):
            thread = threading.Thread(
                target=self._task_worker,
                name=f'agent_worker_thread_{i}',
                daemon=True,
            )
            thread.start()
            self.worker_list.append(thread)
        logger.info('Agent task worker started with %s threads', self.worker_num)

    def stop(self):
        self.should_stop.set()
        for thread in self.worker_list:
            thread.join(timeout=2)
        logger.info('Agent task worker stopped.')

    def _task_worker(self):
        while not self.should_stop.is_set():
            try:
                res = self.mq.brpop(AGENT_TASK_QUEUE_NAME, timeout=30)
                if res is None:
                    continue

                _, value = res
                try:
                    data = json.loads(value)
                except Exception:
                    logger.warning('Invalid agent task payload: %s', value)
                    continue

                if not isinstance(data, dict):
                    logger.warning('Unexpected agent task payload type: %s', type(data).__name__)
                    continue

                self._process_message(data)

            except (ResponseError, NoMQAvailable, TimeoutError) as e:
                logger.error('Redis connection failed in agent task worker: %s', e)
                time.sleep(1)
            except Exception as e:
                logger.exception('Agent task worker loop failed: %s', e)
                time.sleep(1)

    def _process_message(self, data: dict):
        project_uuid = str(data.get('project_uuid') or '').strip()
        if not project_uuid:
            logger.warning('Agent task missing project_uuid: %s', data)
            return
        project_uuid = uuid_str_to_32_chars(project_uuid)

        source_type = str(data.get('source_type') or '').strip()
        if not SourceType.is_valid(source_type):
            logger.warning('Unknown source_type %s, drop task', source_type)
            return

        record_id = data.get('record_id')
        connection_id = data.get('connection_id')
        events = data.get('events')
        if not isinstance(events, list):
            events = []

        self._work_task_handler(
            project_uuid=project_uuid,
            source_type=source_type,
            record_id=record_id,
            connection_id=connection_id,
            events=events,
        )

    def _work_task_handler(
        self,
        project_uuid: str,
        source_type: str,
        record_id,
        connection_id=None,
        events=None,
    ):
        project = self.app.seaqa_db.get_project_by_uuid(project_uuid)
        if not project:
            logger.warning('Project not found when processing agent task: %s', project_uuid)
            return

        events = list(events) if events else []

        if source_type == SourceType.TICKET.value:
            try:
                ticket_agent = TicketAgent(
                    self.app,
                    project,
                    record_id=record_id,
                    events=events,
                )
                ticket_agent.run()
                if ticket_agent.last_run_failed and ticket_agent.last_run_error_message:
                    logger.error('TicketAgent failed for project %s record %s: %s', project_uuid, record_id, ticket_agent.last_run_error_message)
            except Exception as e:
                logger.exception('TicketAgent failed for project %s record %s: %s', project_uuid, record_id, e)

        if source_type == SourceType.GITHUB_ISSUE.value:
            try:
                github_agent = GithubIssueAgent(
                    self.app,
                    project,
                    connection_id=connection_id,
                    record_id=record_id,
                    events=events,
                )
                github_agent.run()
                if github_agent.last_run_failed and github_agent.last_run_error_message:
                    logger.error('GithubIssueAgent failed for project %s connection %s record %s: %s', project_uuid, connection_id, record_id, github_agent.last_run_error_message)
            except Exception as e:
                logger.exception('GithubIssueAgent failed for project %s connection %s record %s: %s', project_uuid, connection_id, record_id, e)

        if source_type == SourceType.DISCOURSE_FORUM.value:
            try:
                discourse_agent = DiscourseAgent(
                    self.app,
                    project,
                    connection_id=connection_id,
                    record_id=record_id,
                    events=events,
                )
                discourse_agent.run()
                if discourse_agent.last_run_failed and discourse_agent.last_run_error_message:
                    logger.error('DiscourseAgent failed for project %s connection %s record %s: %s', project_uuid, connection_id, record_id, discourse_agent.last_run_error_message)
            except Exception as e:
                logger.exception('DiscourseAgent failed for project %s connection %s record %s: %s', project_uuid, connection_id, record_id, e)
