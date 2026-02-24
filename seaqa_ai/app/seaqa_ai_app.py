from seaqa_ai.server.seaqa_ai_http_server import SeaQAAIHttpServer
from seaqa_ai.data_logging.data_logging import DataLogging
from seaqa_ai.managers.ai_utils import AIUtils
from seaqa_ai.utils.llm_api import EmbeddingAPI
from seaqa_ai.utils.seaqa_indexer_api import SeaQAIndexerAPI
from seaqa_ai.utils.seadb_api import SeaDBAPI
from seaqa_ai.utils.event_redis import RedisClient


class SeaQAAIApp(object):
    def __init__(self, config):
        self.redis_client = RedisClient(config.REDIS_HOST, config.REDIS_PORT, config.REDIS_PASSWORD)
        self.data_logger = DataLogging(self.redis_client)
        self.ai_utils = AIUtils(self)
        self.seaqa_indexer_api = SeaQAIndexerAPI(config.SEAQA_INDEXER_INNER_SERVER_URL, config.JWT_PRIVATE_KEY)
        self.seadb_api = SeaDBAPI(config.JWT_PRIVATE_KEY, config.SEADB_SERVER_URL)
        self.embedding_api = EmbeddingAPI(self.data_logger, config.EMBEDDING_MODEL.get('model'), config.EMBEDDING_MODEL.get('type', 'openai'), config.EMBEDDING_MODEL.get('url'), config.EMBEDDING_MODEL.get('key'))
        self.seaqa_ai_http_server = SeaQAAIHttpServer(self)

    def serve_forever(self):
        self.seaqa_ai_http_server.start()
        self.seaqa_ai_http_server.join()
