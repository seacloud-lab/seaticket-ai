import json
import logging
import litellm
from seaqa_ai.utils.constants import MODEL_USAGE_STATISTIC_CHANNEL_NAME, MODEL_REASONING_TIER
from seaqa_ai.config import LLM_MODEL_ID_MODELS_MAP, LLM_MODEL_TIER_MODELS_MAP, DEFAULT_LLM_MODEL

logger = logging.getLogger(__name__)

def get_llm_client_by_model_id(data_logger, model_id):
    model_config = LLM_MODEL_ID_MODELS_MAP.get(model_id, DEFAULT_LLM_MODEL)
    return CompletionAPI(data_logger, model_config.get('model'), model_config.get('type', 'openai'), model_config.get('url'), model_config.get('key'))

def get_llm_client_by_model_tier(data_logger, min_model_tier):
    filtered_tier_model_map = {MODEL_REASONING_TIER.get_level(tier): model for tier, model in LLM_MODEL_TIER_MODELS_MAP.items() if MODEL_REASONING_TIER.get_level(tier) >= MODEL_REASONING_TIER.get_level(min_model_tier)}
    if not filtered_tier_model_map:
        model_config = DEFAULT_LLM_MODEL
    else:
        available_min_tier = min(filtered_tier_model_map.keys())
        model_config = filtered_tier_model_map[available_min_tier]
    return CompletionAPI(data_logger, model_config.get('model'), model_config.get('type', 'openai'), model_config.get('url'), model_config.get('key'))

class BasicLLMAPI:
    def __init__(self, data_logger, model, llm_type='openai', base_url=None, api_key=None, timeout=180):
        self.data_logger = data_logger
        self.timeout = timeout
        if llm_type == 'other':
            llm_type = 'hosted_vllm'
        elif llm_type == 'proxy':
            llm_type = None
            if not api_key:
                api_key = 'not-keys-needed'
        self.llm_type = llm_type
        self.model = f'{llm_type}/{model}' if llm_type else model
        self.model_id = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def logger_usage(self, token_usage, context, model=None):
        """
        logger token usage manually for compatible to stream response
        """
        try:
            self.data_logger.log_data(MODEL_USAGE_STATISTIC_CHANNEL_NAME, json.dumps({
                'model': self.model_id if not model else model,
                'usage': {
                    'prompt_tokens': token_usage['input_tokens'],
                    'completion_tokens': token_usage['output_tokens'],
                    'total_tokens': token_usage['total_tokens']
                },
                'username': context.get('username'),
                'org_id': context.get('org_id'),
                'project_uuid': context.get('project_uuid'),
            }))
        except Exception as e:
            logger.warning(f"Chat completed but failure to log usages: {e}")

    def _logger_usage_from_response(self, response, context, model=None):
        try:
            # try-except statement for compatible to stream response
            return self.logger_usage({
                'input_token': response.usage.prompt_tokens,
                'output_tokens': response.usage.completion_tokens,
                'total_tokens': response.usage.total_tokens,
            }, context, model)
        except:
            pass

class CompletionAPI(BasicLLMAPI):
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

        if 'gpt-5' in self.model_id:
            kwargs['temperature'] = 1

        response = litellm.completion(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            **kwargs
        )

        self._logger_usage_from_response(response, context)
        return response

class EmbeddingAPI(BasicLLMAPI):
    def generate(self, texts, context):
        if isinstance(texts, str):
            texts = [texts]
        response = litellm.embedding(
            model=self.model,
            input=texts,
            api_base=self.base_url,
            api_key=self.api_key,
            custom_llm_provider=self.llm_type,
            timeout=self.timeout
        )
        self._logger_usage_from_response(response, context)
        return response.data
