import re

import litellm

from seaqa_ai.utils.llm_api import BasicLLMAPI


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

        gpt_version = re.search(r'\bgpt-(\d+)\b', self.model_id)
        if gpt_version and int(gpt_version.group(1)) >= 5:
            kwargs['temperature'] = 1
            if kwargs.get('tools'):
                kwargs['reasoning_effort'] = 'none'

        if re.search(r'\bclaude\b', self.model_id, re.IGNORECASE):
            kwargs.pop('temperature', None)

        response = litellm.completion(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            **kwargs
        )

        self._logger_usage_from_response(response, context)
        return response
