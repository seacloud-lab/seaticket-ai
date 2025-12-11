import json
import logging
import litellm
from seaqa_ai.utils.constants import MODEL_USAGE_STATISTIC_CHANNEL_NAME
from seaqa_ai.config import EMBEDDING_MODEL

logger = logging.getLogger(__name__)


class LLMAPI:
    def __init__(self, data_logger, llm_type='openai', base_url=None, api_key=None, model='gpt-4.1', timeout=180):
        self.data_logger = data_logger
        self.timeout = timeout
        if llm_type == 'other':
            llm_type = 'openai'
        elif llm_type == 'proxy':
            llm_type = None
            if not api_key:
                api_key = 'not-keys-needed'
        self.model = f'{llm_type}/{model}' if llm_type else model
        self.model_id = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def completion(self, **kwargs):
        if 'model' in kwargs:
            kwargs.pop('model')

        if 'base_url' in kwargs:
            kwargs.pop('base_url')

        if 'api_key' in kwargs:
            kwargs.pop('api_key')

        context = {}
        if 'context' in kwargs:
            context = kwargs.pop('context')

        response = litellm.completion(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            **kwargs
        )
        self._logger_usage(kwargs, response, context)
        return response

    def _logger_usage(self, kwargs, response, context):
        try:
            if hasattr(response, 'usage') and response.usage:
                self.data_logger.log_data(MODEL_USAGE_STATISTIC_CHANNEL_NAME, json.dumps({
                    'model': self.model_id,
                    'usage': {
                        'prompt_tokens': response.usage.prompt_tokens,
                        'completion_tokens': response.usage.completion_tokens,
                        'total_tokens': response.usage.total_tokens
                    },
                    'username': context.get('username'),
                    'org_id': context.get('org_id'),
                    'project_uuid': context.get('project_uuid'),
                }))
        except Exception as e:
            logger.warning(f"Chat completed but failure to log usages: {e}")

    def generate_embeddings(self, texts, context):
        if isinstance(texts, str):
            texts = [texts]
        if EMBEDDING_MODEL['type'] == 'other':
            custom_llm_provider = 'openai'
        elif EMBEDDING_MODEL['type'] == 'proxy':
            custom_llm_provider = 'openai'
            if not EMBEDDING_MODEL['key']:
                EMBEDDING_MODEL['key'] = 'not-keys-needed'
        else:
            custom_llm_provider = EMBEDDING_MODEL['type']
        response = litellm.embedding(
            model=EMBEDDING_MODEL['model'],
            input=texts,
            api_base=EMBEDDING_MODEL['url'],
            api_key=EMBEDDING_MODEL['key'],
            custom_llm_provider=custom_llm_provider,
            timeout=self.timeout
        )
        self._logger_usage(None, response, context)
        return response.data
