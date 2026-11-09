import json
import logging
import time

from seaqa_ai.config import LLM_MODEL_ID_MODELS_MAP, LLM_MODEL_TIER_MODELS_MAP, DEFAULT_LLM_MODEL
from seaqa_ai.utils.constants import MODEL_USAGE_STATISTIC_CHANNEL_NAME, MODEL_REASONING_TIER, AIScenario, LLMCallPurpose
from seaqa_ai.utils.llm_api.usage import calculate_credit_used

logger = logging.getLogger(__name__)


class BasicLLMAPI:
    def __init__(self, data_logger, model, llm_type='openai', base_url=None, api_key=None, timeout=180):
        self.data_logger = data_logger
        self.timeout = timeout
        if llm_type == 'other':
            llm_type = 'hosted_vllm'
        self.llm_type = llm_type
        self.model = f'{llm_type}/{model}' if llm_type else model
        self.model_id = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def get_credit_used(self, token_usage):
        model_config = LLM_MODEL_ID_MODELS_MAP.get(self.model_id, {})
        return calculate_credit_used(token_usage, model_config.get('price'))

    @staticmethod
    def _extract_cached_tokens_from_usage(usage):
        prompt_details = getattr(usage, 'prompt_tokens_details', None)
        cached_tokens = getattr(prompt_details, 'cached_tokens', 0) if prompt_details else 0
        try:
            return int(cached_tokens)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _usage_from_response(response):
        """Normalize a completion / embedding response usage to input/output/cached tokens."""
        usage = getattr(response, 'usage', None)
        if usage is None:
            return {}
        return {
            'input_tokens': getattr(usage, 'prompt_tokens', 0) or 0,
            'output_tokens': getattr(usage, 'completion_tokens', 0) or 0,
            'total_tokens': getattr(usage, 'total_tokens', 0) or 0,
            'cached_tokens': BasicLLMAPI._extract_cached_tokens_from_usage(usage),
        }

    def _describe_call(self, api, purpose, context, log_fields=None):
        context = context or {}
        fields = {
            'purpose': getattr(purpose, 'value', purpose) or LLMCallPurpose.UNKNOWN.value,
            'model': self.model_id,
            'api': api,
            'scenario': context.get('scenario'),
            'project_uuid': context.get('project_uuid'),
            'session_uuid': context.get('session_uuid'),
            'run_id': context.get('run_id'),
            **(log_fields or {}),
        }
        return ' '.join(f'{key}={value}' for key, value in fields.items() if value is not None)

    @staticmethod
    def _log_call_done(description, started_at, usage):
        usage = usage or {}
        logger.info(
            'LLM call done: %s input_tokens=%s cached_tokens=%s output_tokens=%s duration_ms=%d',
            description,
            usage.get('input_tokens', 0),
            usage.get('cached_tokens', 0),
            usage.get('output_tokens', 0),
            (time.monotonic() - started_at) * 1000,
        )

    @staticmethod
    def _log_call_failed(description, started_at, error):
        logger.warning(
            'LLM call failed: %s duration_ms=%d error=%r',
            description, (time.monotonic() - started_at) * 1000, error,
        )

    def logger_usage(self, token_usage, context, model=None):
        """
        logger token usage manually for compatible to stream response
        """
        try:
            context = context or {}
            self.data_logger.log_data(MODEL_USAGE_STATISTIC_CHANNEL_NAME, json.dumps({
                'model': self.model_id if not model else model,
                'usage': {
                    'prompt_tokens': token_usage.get('input_tokens', 0),
                    'completion_tokens': token_usage.get('output_tokens', 0),
                    'total_tokens': token_usage.get('total_tokens', 0),
                    'cached_tokens': token_usage.get('cached_tokens', 0),
                },
                'org_id': context.get('org_id'),
                'project_uuid': context.get('project_uuid'),
                'session_uuid': context.get('session_uuid'),
                'scenario': context.get('scenario', AIScenario.UNKNOWN.value),
            }))
        except Exception as e:
            logger.warning(f"Chat completed but failure to log usages: {e}")

    def _logger_usage_from_response(self, response, context, model=None):
        try:
            # try-except statement for compatible to stream response
            usage = self._usage_from_response(response)
            if usage:
                return self.logger_usage(usage, context, model)
        except:
            pass


from seaqa_ai.utils.llm_api.completion import CompletionAPI
from seaqa_ai.utils.llm_api.embedding import EmbeddingAPI
from seaqa_ai.utils.llm_api.responses import ResponsesAPI, ResponsesResult
from seaqa_ai.utils.llm_api.responses.constants import STREAM_TEXT_DELTA, STREAM_REASONING_DELTA, STREAM_COMPLETED


def get_completion_client_by_model_id(data_logger, model_id):
    model_config = LLM_MODEL_ID_MODELS_MAP.get(model_id, DEFAULT_LLM_MODEL) if model_id else DEFAULT_LLM_MODEL
    return CompletionAPI(data_logger, model_config.get('model'), model_config.get('type', 'openai'), model_config.get('url'), model_config.get('key'))

def get_completion_client_by_model_tier(data_logger, tier):
    if MODEL_REASONING_TIER.is_valid(tier):
        model_config = LLM_MODEL_TIER_MODELS_MAP.get(tier, DEFAULT_LLM_MODEL)
    else:
        model_config = DEFAULT_LLM_MODEL
    return CompletionAPI(data_logger, model_config.get('model'), model_config.get('type', 'openai'), model_config.get('url'), model_config.get('key'))

def get_responses_client_by_model_id(data_logger, model_id):
    model_config = LLM_MODEL_ID_MODELS_MAP.get(model_id, DEFAULT_LLM_MODEL) if model_id else DEFAULT_LLM_MODEL
    return ResponsesAPI(data_logger, model_config.get('model'), model_config.get('type', 'openai'), model_config.get('url'), model_config.get('key'))
