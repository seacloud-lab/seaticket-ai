from seaqa_ai.server.seaqa_ai_http_server import SeaQAAIHttpServer
from seaqa_ai.data_logging.data_logging import DataLogging
from seaqa_ai.text_processing.text_processing_manager import TextProcessingManager
from seaqa_ai.utils.seaqa_index_api import SeaQAIndexAPI
from seaqa_ai.utils.openai_api import OpenAIAPI
from seaqa_ai.config import WEB_CRAWL_INDEX_SERVER_URL, JWT_PRIVATE_KEY


class SeaQAAIApp(object):
    def __init__(self, config):
        self.config = config
        self.data_logger = DataLogging(config.REDIS_HOST, config.REDIS_PORT, config.REDIS_PASSWORD)
        llm_type = config.LLM_TYPE
        base_url = config.LLM_URL
        api_key = config.LLM_KEY
        model = config.LLM_MODEL
        if llm_type not in ['openai-proxy', 'openai', 'other']:
            raise Exception('unknown llm type')
        self.openai_api = OpenAIAPI(llm_type, base_url, api_key, model)
        self.openai_api.init(self.data_logger)

        self.text_processing_manager = TextProcessingManager(self, config.LLM_TYPE)
        self.seaqa_index_api = SeaQAIndexAPI(self, WEB_CRAWL_INDEX_SERVER_URL, JWT_PRIVATE_KEY)
        self.seaqa_ai_http_server = SeaQAAIHttpServer(self)

    def serve_forever(self):
        self.seaqa_ai_http_server.start()
