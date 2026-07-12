import json
from dataclasses import dataclass, field

from seaqa_ai.utils.llm_api.responses.constants import STREAM_TEXT_DELTA, STREAM_REASONING_DELTA, STREAM_COMPLETED


@dataclass
class ResponsesResult:
    content: str = ''
    tool_calls: list = field(default_factory=list)
    reasoning: str = ''
    usage: dict = field(default_factory=dict)
    response_id: str = ''
    raw: object = None


class ResponsesParser:
    @staticmethod
    def get(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def stringify(value):
        if value is None:
            return ''
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def extract_text_parts(item):
        texts = []
        for part in ResponsesParser.get(item, 'content') or []:
            text = ResponsesParser.get(part, 'text')
            if text:
                texts.append(text)
        return texts

    @staticmethod
    def extract_reasoning_text(item):
        texts = []
        for summary in ResponsesParser.get(item, 'summary') or []:
            text = ResponsesParser.get(summary, 'text')
            if text:
                texts.append(text)
        if not texts:
            texts = ResponsesParser.extract_text_parts(item)
        return '\n'.join(texts)

    @staticmethod
    def function_call_item_to_tool_call(item, index):
        return {
            'type': 'function_call',
            'call_id': ResponsesParser.get(item, 'call_id') or ResponsesParser.get(item, 'id') or f'call_{index}',
            'name': ResponsesParser.get(item, 'name') or '',
            'arguments': ResponsesParser.stringify(ResponsesParser.get(item, 'arguments')),
        }

    @staticmethod
    def extract_usage(response):
        usage = ResponsesParser.get(response, 'usage')
        if usage is None:
            return {}

        input_tokens = ResponsesParser.get(usage, 'input_tokens')
        if input_tokens is None:
            input_tokens = ResponsesParser.get(usage, 'prompt_tokens')
        output_tokens = ResponsesParser.get(usage, 'output_tokens')
        if output_tokens is None:
            output_tokens = ResponsesParser.get(usage, 'completion_tokens')
        total_tokens = ResponsesParser.get(usage, 'total_tokens')
        input_details = ResponsesParser.get(usage, 'input_tokens_details') or {}
        cached_tokens = ResponsesParser.get(input_details, 'cached_tokens') or 0

        result = {}
        if input_tokens is not None:
            result['input_tokens'] = input_tokens
        if output_tokens is not None:
            result['output_tokens'] = output_tokens
        if total_tokens is not None:
            result['total_tokens'] = total_tokens
        elif input_tokens is not None and output_tokens is not None:
            result['total_tokens'] = input_tokens + output_tokens
        if cached_tokens is not None:
            result['cached_tokens'] = cached_tokens
        return result

    @staticmethod
    def parse_output(response):
        content_chunks = []
        reasoning_chunks = []
        tool_calls = []

        for index, item in enumerate(ResponsesParser.get(response, 'output') or []):
            item_type = ResponsesParser.get(item, 'type')
            if item_type == 'function_call':
                tool_calls.append(ResponsesParser.function_call_item_to_tool_call(item, index))
            elif item_type == 'reasoning':
                text = ResponsesParser.extract_reasoning_text(item)
                if text:
                    reasoning_chunks.append(text)
            elif item_type == 'message' or ResponsesParser.get(item, 'role') == 'assistant':
                content_chunks.extend(ResponsesParser.extract_text_parts(item))

        content = ''.join(content_chunks)
        if not content:
            output_text = ResponsesParser.get(response, 'output_text')
            if isinstance(output_text, str):
                content = output_text

        return ResponsesResult(
            content=content,
            tool_calls=tool_calls,
            reasoning='\n'.join(reasoning_chunks),
            usage=ResponsesParser.extract_usage(response),
            response_id=ResponsesParser.get(response, 'id') or '',
            raw=response,
        )

    @staticmethod
    def iter_stream(events):
        for event in events:
            event_type = ResponsesParser.get(event, 'type')
            if event_type == 'response.output_text.delta':
                delta = ResponsesParser.get(event, 'delta') or ''
                if delta:
                    yield (STREAM_TEXT_DELTA, delta)
            elif event_type in ('response.reasoning_summary_text.delta', 'response.reasoning_text.delta'):
                delta = ResponsesParser.get(event, 'delta') or ''
                if delta:
                    yield (STREAM_REASONING_DELTA, delta)
            elif event_type == 'response.completed':
                response = ResponsesParser.get(event, 'response')
                if response is not None:
                    yield (STREAM_COMPLETED, ResponsesParser.parse_output(response))
            elif event_type == 'error':
                raise RuntimeError(f'Responses API stream error event: {event!r}')
