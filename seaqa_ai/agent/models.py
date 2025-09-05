from smolagents.models import ApiModel, ChatMessage
from smolagents.monitoring import TokenUsage
from smolagents import Tool
from typing import Any


class LiteLLMModel(ApiModel):
    """
    Parameters:
        client (`Any`, **optional**):
            Pre-configured API client instance
        model_id (`str`):
            The model identifier to use on the server (e.g. "gpt-3.5-turbo").
        custom_role_conversions (`dict[str, str]`, *optional*):
            Custom role conversion mapping to convert message roles in others.
            Useful for specific models that do not support specific message roles like "system".
        flatten_messages_as_text (`bool`, *optional*): Whether to flatten messages as text.
            Defaults to `True` for models that start with "ollama", "groq", "cerebras".
        **kwargs:
            Additional keyword arguments to pass to the OpenAI API.
    """

    def __init__(
        self,
        client: Any,
        model_id,
        custom_role_conversions: dict[str, str] | None = None,
        flatten_messages_as_text: bool | None = None,
        **kwargs,
    ):
        flatten_messages_as_text = (
            flatten_messages_as_text
            if flatten_messages_as_text is not None
            else model_id.startswith(("ollama", "groq", "cerebras"))
        )
        super().__init__(
            model_id=model_id,
            client=client,
            custom_role_conversions=custom_role_conversions,
            flatten_messages_as_text=flatten_messages_as_text,
            **kwargs,
        )

    def generate(
        self,
        messages: list[ChatMessage | dict],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Tool] | None = None,
        **kwargs,
    ) -> ChatMessage:
        completion_kwargs = self._prepare_completion_kwargs(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            convert_images_to_image_urls=True,
            custom_role_conversions=self.custom_role_conversions,
            **kwargs,
        )
        if hasattr(self, 'context'):
            completion_kwargs['context'] = self.context
        self._apply_rate_limit()
        response = self.client.run(**completion_kwargs)
        return ChatMessage.from_dict(
            response.choices[0].message.model_dump(include={"role", "content", "tool_calls"}),
            raw=response,
            token_usage=TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            ),
        )
