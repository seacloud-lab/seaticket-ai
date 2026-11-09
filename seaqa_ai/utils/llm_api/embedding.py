import time

import litellm

from seaqa_ai.utils.llm_api import BasicLLMAPI


class EmbeddingAPI(BasicLLMAPI):
    def _embedding(self, texts, context, purpose, log_fields):
        description = self._describe_call('embedding', purpose, context, log_fields)
        started_at = time.monotonic()
        try:
            response = litellm.embedding(
                model=self.model,
                input=texts,
                api_base=self.base_url,
                api_key=self.api_key,
                custom_llm_provider=self.llm_type,
                timeout=self.timeout
            )
        except Exception as error:
            self._log_call_failed(description, started_at, error)
            raise
        self._log_call_done(description, started_at, self._usage_from_response(response))

        self._logger_usage_from_response(response, context)
        return response

    def generate(self, text, context, purpose=None, log_fields=None):
        response = self._embedding([text], context, purpose, log_fields)

        if len(response.data) != 1 or not response.data[0].get('embedding'):
            raise ConnectionError('Failure to generate embedding')

        return response.data[0]['embedding']

    def batch_generate(self, texts, context, purpose=None, log_fields=None):
        response = self._embedding(texts, context, purpose, {'count': len(texts), **(log_fields or {})})

        if len(response.data) != len(texts):
            raise ConnectionError('Failure to generate embedding')

        embeddings = []
        for item in sorted(response.data, key=lambda x: x.get('index', 0)):
            embedding = item.get('embedding')
            if not embedding:
                raise ConnectionError('Failure to generate embedding')
            embeddings.append(embedding)

        return embeddings
