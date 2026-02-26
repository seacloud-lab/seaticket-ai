import smolagents.models

class LiteLLMModel(smolagents.models.LiteLLMModel):
    def __init__(self, client, custom_role_conversions = None, flatten_messages_as_text = None, **kwargs):
        if 'gpt-5' in client.model_id:
            super().__init__(client.model_id, client.base_url, client.api_key, custom_role_conversions, flatten_messages_as_text, client=client, temperature=1, **kwargs)
        else:
            super().__init__(client.model_id, client.base_url, client.api_key, custom_role_conversions, flatten_messages_as_text, client=client, **kwargs)
