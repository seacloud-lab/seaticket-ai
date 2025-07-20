from seaqa_ai.server.seaqa_ai_http_server import SeaQAAIHttpServer
from seaqa_ai.data_logging.data_logging import DataLogging
from seaqa_ai.text_processing.text_processing_manager import TextProcessingManager


class SeaQAAIApp(object):
    def __init__(self, config):
        self.config = config
        self.data_logger = DataLogging(config.REDIS_HOST, config.REDIS_PORT, config.REDIS_PASSWORD)
        if config.LLM_TYPE == 'openai-proxy':
            from seaqa_ai.utils.openai_api import OpenAIAPI
            self.openai_api = OpenAIAPI(config.LLM_URL)
        elif config.LLM_TYPE == 'openai':
            from seaqa_ai.utils.openai_api import OpenAIAPI
            self.openai_api = OpenAIAPI(api_key=config.LLM_KEY)
        else:
            raise Exception('unknown llm type')
        self.openai_api.init(self.data_logger)

        self.text_processing_manager = TextProcessingManager(self, config.LLM_TYPE)
        self.seaqa_ai_http_server = SeaQAAIHttpServer(self)

    def serve_forever(self):
        self.seaqa_ai_http_server.start()
