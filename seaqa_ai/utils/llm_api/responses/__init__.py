import re
import time

import litellm

from seaqa_ai.utils.llm_api import BasicLLMAPI
from seaqa_ai.utils.llm_api.responses.constants import STREAM_TEXT_DELTA, STREAM_REASONING_DELTA, STREAM_COMPLETED
from seaqa_ai.utils.llm_api.responses.parser import ResponsesParser, ResponsesResult


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

        gpt_version = re.search(r'\bgpt-(\d+)\b', self.model_id)
        if gpt_version and int(gpt_version.group(1)) >= 5:
            kwargs['temperature'] = 1

        if re.search(r'\bclaude\b', self.model_id, re.IGNORECASE):
            kwargs.pop('temperature', None)
        return request

    def responses(self, purpose=None, log_fields=None, **kwargs):
        """Run one non-streaming Responses turn."""
        context = kwargs.pop('context', {})
        request = self._build_responses_request(kwargs)
        description = self._describe_call('responses', purpose, context, log_fields)
        started_at = time.monotonic()
        try:
            raw = litellm.responses(
                model=self.model,
                **self._client_kwargs(),
                timeout=self.timeout,
                store=False,
                **request,
                **kwargs,
            )
            result = ResponsesParser.parse_output(raw)
        except Exception as error:
            self._log_call_failed(description, started_at, error)
            raise
        self._log_call_done(description, started_at, result.usage)

        if result.usage:
            self.logger_usage(result.usage, context)
        return result

    def responses_stream(self, purpose=None, log_fields=None, **kwargs):
        """Stream one Responses turn."""
        context = kwargs.pop('context', {}) or {}
        request = self._build_responses_request(kwargs)
        description = self._describe_call('responses_stream', purpose, context, log_fields)
        started_at = time.monotonic()
        try:
            events = litellm.responses(
                model=self.model,
                **self._client_kwargs(),
                timeout=self.timeout,
                store=False,
                stream=True,
                **request,
                **kwargs,
            )
        except Exception as error:
            self._log_call_failed(description, started_at, error)
            raise
        return self._iter_stream_with_logging(events, description, started_at)

    def _iter_stream_with_logging(self, events, description, started_at):
        try:
            for kind, payload in ResponsesParser.iter_stream(events):
                if kind == STREAM_COMPLETED:
                    self._log_call_done(description, started_at, payload.usage)
                yield kind, payload
        except Exception as error:
            self._log_call_failed(description, started_at, error)
            raise
