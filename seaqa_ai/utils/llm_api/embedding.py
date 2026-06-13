import litellm

from seaqa_ai.utils.llm_api import BasicLLMAPI


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
