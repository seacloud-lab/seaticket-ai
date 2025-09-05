import json
import logging
from litellm import completion
from seaqa_ai.utils import LLMChatCompletionException
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

    def _completion(self, **kwargs):
        return completion(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            **kwargs
        )

    def run(self, messages, context={}, **kwargs):
        """
        llm completion with a seaAI context:
        """

        # messages valid check
        assert isinstance(messages, list) or isinstance(messages, tuple)

        kwargs.update({'messages': messages})

        try:
            if 'context' in kwargs:
                kwargs.pop('context')
            response = self._completion(**kwargs)
            # self._logger_usage(response, context)
            return response
        except Exception as e:
            logger.error('Chat completion error: %s', str(e))
            raise LLMChatCompletionException('LLM chat completion error: %s' % e)

    def _logger_usage(self, response, context):
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
                    'assistant_uuid': context.get('assistant_uuid'),
                }))
        except Exception as e:
            logger.warning(f"Chat completed but failure to log usages: {e}")
