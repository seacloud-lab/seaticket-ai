import logging

from seaqa_ai.http_server.seaqa_ai_http_server import SeaQAAIHttpServer
from seaqa_ai.utils.data_logging import DataLogging
from seaqa_ai.services.ai_services import AIServices
from seaqa_ai.utils.llm_api import EmbeddingAPI
from seaqa_ai.utils.seaqa_indexer_api import SeaQAIndexerAPI
from seaqa_ai.utils.seaqa_web_api import SeaQAWebAPI
from seaqa_ai.utils.seadb import SeaDB
from seaqa_ai.utils.event_redis import RedisClient
from seaqa_ai.utils.username_resolver import UsernameResolver
from seaqa_ai.db import init_db_session_class
from seaqa_ai.agent.events_manager import EventsManager
from seaqa_ai.agent.tasks_manager import TaskManager
from seaqa_ai.agent.tasks_manager.task_worker import AgentTaskWorker
from seaqa_ai.utils.seaqa_db import SeaqaDB

logger = logging.getLogger(__name__)


class SeaQAAIApp(object):
    def __init__(self, config):
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)
        self.username_resolver = UsernameResolver(self.db_session_class)
        self.redis_client = RedisClient(config.REDIS_HOST, config.REDIS_PORT, config.REDIS_PASSWORD)
        self.data_logger = DataLogging(self.redis_client)
        self.ai_services = AIServices(self)
        self.seaqa_web_api = SeaQAWebAPI(config.SEAQA_WEB_INNER_SERVER_URL, config.JWT_PRIVATE_KEY)
        self.seaqa_indexer_api = SeaQAIndexerAPI(config.SEAQA_INDEXER_INNER_SERVER_URL, config.JWT_PRIVATE_KEY)
        self.seadb = SeaDB(config.SEADB_SERVER_URL, config.SEADB_USER, config.SEADB_PASSWORD)
        self.seaqa_db = SeaqaDB(self.db_session_class)
        self.embedding_api = EmbeddingAPI(self.data_logger, config.EMBEDDING_MODEL.get('model'), config.EMBEDDING_MODEL.get('type', 'openai'), config.EMBEDDING_MODEL.get('url'), config.EMBEDDING_MODEL.get('key'))
        self.task_manager = TaskManager(self.redis_client.connection)
        self.events_manager = EventsManager(self)
        self.agent_task_worker = AgentTaskWorker(self)
        self.seaqa_ai_http_server = SeaQAAIHttpServer(self)
        self._stopped = False

    def serve_forever(self):
        self.agent_task_worker.start()
        self.events_manager.start()
        self.seaqa_ai_http_server.start()
        self.seaqa_ai_http_server.join()

    def stop(self):
        """Stop all background components gracefully."""
        if self._stopped:
            return
        self._stopped = True

        try:
            self.events_manager.stop()
        except Exception as e:
            logger.exception('Failed to stop EventsManager: %s', e)

        try:
            self.agent_task_worker.stop()
        except Exception as e:
            logger.exception('Failed to stop AgentTaskWorker: %s', e)

        try:
            self.seaqa_ai_http_server.stop()
            self.seaqa_ai_http_server.join(timeout=5)
        except Exception as e:
            logger.exception('Failed to stop SeaQAAIHttpServer: %s', e)
