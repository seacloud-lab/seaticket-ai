import json
import logging
import litellm
from seaqa_ai.utils.constants import MODEL_USAGE_STATISTIC_CHANNEL_NAME

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
                    'model': response.model,
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
        response = litellm.embedding(
            model='text-embedding-3-small',
            input=texts,
            api_key=self.api_key,
            api_base=self.base_url,
            timeout=self.timeout
        )
        return response.data
