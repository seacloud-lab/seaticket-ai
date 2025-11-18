from seaqa_ai.server.seaqa_ai_http_server import SeaQAAIHttpServer
from seaqa_ai.data_logging.data_logging import DataLogging
from seaqa_ai.managers.chat_manager import ChatManager
from seaqa_ai.managers.assistant_manager import AIUtils
from seaqa_ai.utils.seaqa_indexer_api import SeaQAIndexerAPI
from seaqa_ai.utils.llm_api import LLMAPI
from seaqa_ai.agent.agent_manager import AgentManager


class SeaQAAIApp(object):
    def __init__(self, config):
        self.data_logger = DataLogging(config.REDIS_HOST, config.REDIS_PORT, config.REDIS_PASSWORD)

        self.llm_api = LLMAPI(self.data_logger, config.LLM_TYPE, config.LLM_URL, config.LLM_KEY, config.LLM_MODEL)

        self.chat_manager = ChatManager(self)
        self.ai_utils = AIUtils(self)
        self.seaqa_indexer_api = SeaQAIndexerAPI(config.SEAQA_INDEXER_SERVER_URL, config.JWT_PRIVATE_KEY)

        self.agent_manager = AgentManager(self)

        self.seaqa_ai_http_server = SeaQAAIHttpServer(self)

    def serve_forever(self):
        self.seaqa_ai_http_server.start()
        self.seaqa_ai_http_server.join()
