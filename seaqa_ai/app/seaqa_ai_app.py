from seaqa_ai.server.seaqa_ai_http_server import SeaQAAIHttpServer
from seaqa_ai.data_logging.data_logging import DataLogging
from seaqa_ai.managers.chat_manager import ChatManager
from seaqa_ai.managers.ai_utils import AIUtils
from seaqa_ai.utils.seaqa_indexer_api import SeaQAIndexerAPI
from seaqa_ai.utils.llm_api import LLMAPI
from seaqa_ai.agent.agent_manager import AgentManager


class SeaQAAIApp(object):
    def __init__(self, config):
        self.data_logger = DataLogging(config.REDIS_HOST, config.REDIS_PORT, config.REDIS_PASSWORD)

        default_model_config = config.get_default_model_config()
        self.llm_api = LLMAPI(
            self.data_logger,
            default_model_config['type'],
            default_model_config['url'],
            default_model_config['key'],
            default_model_config['model']
        )

        self.chat_manager = ChatManager(self)
        self.ai_utils = AIUtils(self)
        self.seaqa_indexer_api = SeaQAIndexerAPI(config.SEAQA_INDEXER_INNER_SERVER_URL, config.JWT_PRIVATE_KEY)

        self.agent_manager = AgentManager(self)

        self.seaqa_ai_http_server = SeaQAAIHttpServer(self)

    def serve_forever(self):
        self.seaqa_ai_http_server.start()
        self.seaqa_ai_http_server.join()
