import json
import logging
import litellm
import smolagents.models
from seaqa_ai.utils.constants import MODEL_USAGE_STATISTIC_CHANNEL_NAME
from seaqa_ai.config import EMBEDDING_MODEL, LLM_MODEL_ID_MODELS_MAP, LLM_MODEL_TIER_MODELS_MAP, DEFAULT_LLM_MODEL

logger = logging.getLogger(__name__)

def get_llm_client_by_model_id(data_logger, model_id):
    model_config = LLM_MODEL_ID_MODELS_MAP.get(model_id, DEFAULT_LLM_MODEL)
    return LLMAPI(data_logger, model_config['type'], model_config['url'], model_config['key'], model_config['model'])

def get_llm_client_by_model_tier(data_logger, min_model_tier):
    filtered_tier_model_map = {tier: model for tier, model in LLM_MODEL_TIER_MODELS_MAP.items() if tier >= min_model_tier}
    if not filtered_tier_model_map:
        model_config = DEFAULT_LLM_MODEL
    else:
        available_min_tier = min(filtered_tier_model_map.keys())
        model_config = filtered_tier_model_map[available_min_tier]
    return LLMAPI(data_logger, model_config['type'], model_config['url'], model_config['key'], model_config['model'])

class LLMAPI:
    def __init__(self, data_logger, llm_type='openai', base_url=None, api_key=None, model='gpt-4.1', timeout=180):
        self.data_logger = data_logger
        self.timeout = timeout
        if llm_type == 'other':
            llm_type = 'hosted_vllm'
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

        if 'gpt-5' in self.model_id:
            kwargs['temperature'] = 1

        response = litellm.completion(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            **kwargs
        )

        self._logger_usage(response, context)
        return response

    def _logger_usage(self, response, context, model=None):
        try:
            if hasattr(response, 'usage') and response.usage:
                self.data_logger.log_data(MODEL_USAGE_STATISTIC_CHANNEL_NAME, json.dumps({
                    'model': self.model_id if not model else model,
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
        self._logger_usage(response, context, EMBEDDING_MODEL['model'])
        return response.data
    
class LiteLLMModel(smolagents.models.LiteLLMModel):
    def __init__(self, model_id = None, api_base = None, api_key = None, custom_role_conversions = None, flatten_messages_as_text = None, **kwargs):
        if 'gpt-5' in model_id:
            super().__init__(model_id, api_base, api_key, custom_role_conversions, flatten_messages_as_text, temperature=1, **kwargs)
        else:
            super().__init__(model_id, api_base, api_key, custom_role_conversions, flatten_messages_as_text, **kwargs)
