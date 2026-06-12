import logging

import litellm

from seaqa_ai.utils.llm_api import BasicLLMAPI
from seaqa_ai.utils.llm_api.responses.constants import STREAM_TEXT_DELTA, STREAM_REASONING_DELTA, STREAM_COMPLETED
from seaqa_ai.utils.llm_api.responses.parser import ResponsesParser, ResponsesResult

logger = logging.getLogger(__name__)


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

        result = ResponsesParser.parse_output(raw)
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
        return ResponsesParser.iter_stream(events)
