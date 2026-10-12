import litellm

from seaqa_ai.utils.llm_api import BasicLLMAPI
from seaqa_ai.utils.llm_api.constants import TEMPERATURE_1_MODEL_PREFIX


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

        if any([
            model_prefix in self.model_id
            for model_prefix in TEMPERATURE_1_MODEL_PREFIX
        ]):
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
