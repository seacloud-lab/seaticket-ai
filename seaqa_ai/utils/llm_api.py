import json
import logging
import litellm
from dataclasses import dataclass, field
from seaqa_ai.utils.constants import MODEL_USAGE_STATISTIC_CHANNEL_NAME, MODEL_REASONING_TIER, AIScenario
from seaqa_ai.config import LLM_MODEL_ID_MODELS_MAP, LLM_MODEL_TIER_MODELS_MAP, DEFAULT_LLM_MODEL

logger = logging.getLogger(__name__)

STREAM_TEXT_DELTA = 'text_delta'
STREAM_REASONING_DELTA = 'reasoning_delta'
STREAM_COMPLETED = 'completed'


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _stringify(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


@dataclass
class ResponsesResult:
    content: str = ''
    tool_calls: list = field(default_factory=list)
    reasoning: str = ''
    usage: dict = field(default_factory=dict)
    response_id: str = ''
    raw: object = None


def _extract_text_parts(item):
    texts = []
    for part in _get(item, 'content') or []:
        text = _get(part, 'text')
        if text:
            texts.append(text)
    return texts


def _extract_reasoning_text(item):
    texts = []
    for summary in _get(item, 'summary') or []:
        text = _get(summary, 'text')
        if text:
            texts.append(text)
    if not texts:
        texts = _extract_text_parts(item)
    return '\n'.join(texts)



def _function_call_item_to_tool_call(item, index):
    return {
        'type': 'function_call',
        'call_id': _get(item, 'call_id') or _get(item, 'id') or f'call_{index}',
        'name': _get(item, 'name') or '',
        'arguments': _stringify(_get(item, 'arguments')),
    }


def _extract_usage(response):
    usage = _get(response, 'usage')
    if usage is None:
        return {}

    input_tokens = _get(usage, 'input_tokens')
    if input_tokens is None:
        input_tokens = _get(usage, 'prompt_tokens')
    output_tokens = _get(usage, 'output_tokens')
    if output_tokens is None:
        output_tokens = _get(usage, 'completion_tokens')
    total_tokens = _get(usage, 'total_tokens')

    result = {}
    if input_tokens is not None:
        result['input_tokens'] = input_tokens
    if output_tokens is not None:
        result['output_tokens'] = output_tokens
    if total_tokens is not None:
        result['total_tokens'] = total_tokens
    elif input_tokens is not None and output_tokens is not None:
        result['total_tokens'] = input_tokens + output_tokens
    return result


def _parse_responses_output(response):
    content_chunks = []
    reasoning_chunks = []
    tool_calls = []

    for index, item in enumerate(_get(response, 'output') or []):
        item_type = _get(item, 'type')
        if item_type == 'function_call':
            tool_calls.append(_function_call_item_to_tool_call(item, index))
        elif item_type == 'reasoning':
            text = _extract_reasoning_text(item)
            if text:
                reasoning_chunks.append(text)
        elif item_type == 'message' or _get(item, 'role') == 'assistant':
            content_chunks.extend(_extract_text_parts(item))

    content = ''.join(content_chunks)
    if not content:
        output_text = _get(response, 'output_text')
        if isinstance(output_text, str):
            content = output_text

    return ResponsesResult(
        content=content,
        tool_calls=tool_calls,
        reasoning='\n'.join(reasoning_chunks),
        usage=_extract_usage(response),
        response_id=_get(response, 'id') or '',
        raw=response,
    )


def _iter_responses_stream(events):
    for event in events:
        event_type = _get(event, 'type')
        if event_type == 'response.output_text.delta':
            delta = _get(event, 'delta') or ''
            if delta:
                yield (STREAM_TEXT_DELTA, delta)
        elif event_type in ('response.reasoning_summary_text.delta', 'response.reasoning_text.delta'):
            delta = _get(event, 'delta') or ''
            if delta:
                yield (STREAM_REASONING_DELTA, delta)
        elif event_type == 'response.completed':
            response = _get(event, 'response')
            if response is not None:
                yield (STREAM_COMPLETED, _parse_responses_output(response))
        elif event_type == 'error':
            raise RuntimeError(f'Responses API stream error event: {event!r}')

def get_llm_client_by_model_id(data_logger, model_id):
    model_config = LLM_MODEL_ID_MODELS_MAP.get(model_id, DEFAULT_LLM_MODEL) if model_id else DEFAULT_LLM_MODEL
    return CompletionAPI(data_logger, model_config.get('model'), model_config.get('type', 'openai'), model_config.get('url'), model_config.get('key'))

def get_responses_client_by_model_id(data_logger, model_id):
    model_config = LLM_MODEL_ID_MODELS_MAP.get(model_id, DEFAULT_LLM_MODEL) if model_id else DEFAULT_LLM_MODEL
    return ResponsesAPI(data_logger, model_config.get('model'), model_config.get('type', 'openai'), model_config.get('url'), model_config.get('key'))

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
                'org_id': context.get('org_id'),
                'project_uuid': context.get('project_uuid'),
                'scenario': context.get('scenario', AIScenario.UNKNOWN.value),
            }))
        except Exception as e:
            logger.warning(f"Chat completed but failure to log usages: {e}")

    def _logger_usage_from_response(self, response, context, model=None):
        try:
            # try-except statement for compatible to stream response
            return self.logger_usage({
                'input_tokens': response.usage.prompt_tokens,
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

class ResponsesAPI(BasicLLMAPI):
    def _client_kwargs(self):
        kwargs = {
            'api_key': self.api_key,
        }
        if self.llm_type == 'hosted_vllm':
            kwargs['api_base'] = self.base_url
        else:
            kwargs['base_url'] = self.base_url
        return kwargs

    def _build_responses_request(self, kwargs):
        """Build Responses request fields from Responses-native kwargs."""
        for client_key in ('model', 'base_url', 'api_base', 'api_key'):
            kwargs.pop(client_key, None)

        request = {}
        for key in ('input', 'tools', 'tool_choice'):
            value = kwargs.pop(key, None)
            if value is not None:
                request[key] = value

        if 'gpt-5' in self.model_id:
            kwargs['temperature'] = 1
        return request

    def responses(self, **kwargs):
        """Run one non-streaming Responses turn."""
        context = kwargs.pop('context', {})
        request = self._build_responses_request(kwargs)
        logger.info(
            'responses LLM responses start. model=%s timeout=%s stream=False session_uuid=%s project_uuid=%s',
            self.model,
            self.timeout,
            context.get('session_uuid'),
            context.get('project_uuid'),
        )

        raw = litellm.responses(
            model=self.model,
            **self._client_kwargs(),
            timeout=self.timeout,
            store=False,
            **request,
            **kwargs,
        )

        result = _parse_responses_output(raw)
        if result.usage:
            self.logger_usage(result.usage, context)
        return result

    def responses_stream(self, **kwargs):
        """Stream one Responses turn."""
        context = kwargs.pop('context', {}) or {}
        request = self._build_responses_request(kwargs)
        logger.info(
            'responses LLM responses start. model=%s timeout=%s stream=True session_uuid=%s project_uuid=%s',
            self.model,
            self.timeout,
            context.get('session_uuid'),
            context.get('project_uuid'),
        )

        events = litellm.responses(
            model=self.model,
            **self._client_kwargs(),
            timeout=self.timeout,
            store=False,
            stream=True,
            **request,
            **kwargs,
        )
        return _iter_responses_stream(events)

class EmbeddingAPI(BasicLLMAPI):
    def generate(self, text, context):
        response = litellm.embedding(
            model=self.model,
            input=[text],
            api_base=self.base_url,
            api_key=self.api_key,
            custom_llm_provider=self.llm_type,
            timeout=self.timeout
        )

        self._logger_usage_from_response(response, context)

        if len(response.data) != 1 or not response.data[0].get('embedding'):
            raise ConnectionError('Failure to generate embedding')

        return response.data[0]['embedding']

    def batch_generate(self, texts, context):
        response = litellm.embedding(
            model=self.model,
            input=texts,
            api_base=self.base_url,
            api_key=self.api_key,
            custom_llm_provider=self.llm_type,
            timeout=self.timeout
        )

        self._logger_usage_from_response(response, context)

        if len(response.data) != len(texts):
            raise ConnectionError('Failure to generate embedding')

        embeddings = []
        for item in sorted(response.data, key=lambda x: x.get('index', 0)):
            embedding = item.get('embedding')
            if not embedding:
                raise ConnectionError('Failure to generate embedding')
            embeddings.append(embedding)

        return embeddings
