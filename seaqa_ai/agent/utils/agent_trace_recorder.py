import json
from copy import deepcopy
from seaqa_ai.config import THOUGHT_PROCESS_RECORD_TOOL_DETAILS
from seaqa_ai.utils import remove_sources_content_and_snippets


class AgentTraceRecorder:
    MAX_STRING_CHARS = 1000
    MAX_PROMPT_CONTENT_CHARS = 5000
    SEARCH_TOOLS_WITH_LARGE_SOURCES = {'documents_search', 'issues_search'}

    def __init__(self, enabled=False):
        self.enabled = bool(enabled)

    def _truncate_string(self, value, max_chars):
        if not isinstance(value, str):
            return value
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + f'\n... [truncated, original_length={len(value)}]'

    def _to_serializable(self, value):
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return self._truncate_string(value, self.MAX_STRING_CHARS)
        if isinstance(value, list):
            return [self._to_serializable(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self._to_serializable(item)
                for key, item in value.items()
            }
        return str(value)

    def _parse_json_observation(self, observation):
        if not isinstance(observation, str):
            return observation
        try:
            return json.loads(observation)
        except Exception:
            return observation

    def _normalize_observation(self, observation, tool_name=None):
        normalized_observation = self._parse_json_observation(observation)
        if not THOUGHT_PROCESS_RECORD_TOOL_DETAILS and tool_name in self.SEARCH_TOOLS_WITH_LARGE_SOURCES and isinstance(normalized_observation, list):
            normalized_observation = remove_sources_content_and_snippets(deepcopy(normalized_observation))
        return self._to_serializable(normalized_observation)

    def _normalize_prompt_message(self, message):
        if not isinstance(message, dict):
            return self._to_serializable(message)
        normalized = {}
        for key, value in message.items():
            if key == 'tool_call_id':
                # Skip tool_call_id field
                continue
            if key == 'tool_calls' and isinstance(value, list):
                normalized[key] = [
                    {
                        tool_call_key: self._to_serializable(tool_call_value)
                        for tool_call_key, tool_call_value in tool_call.items()
                        if tool_call_key != 'id' # Skip id field
                    }
                    for tool_call in value
                    if isinstance(tool_call, dict)
                ]
            elif key == 'content' and isinstance(value, str):
                normalized[key] = self._truncate_string(value, self.MAX_PROMPT_CONTENT_CHARS)
            else:
                normalized[key] = self._to_serializable(value)
        return normalized

    def _normalize_prompt(self, prompt):
        if not isinstance(prompt, list):
            return self._to_serializable(prompt)

        return [
            self._normalize_prompt_message(message)
            for message in prompt
        ]

    def _normalize_error(self, error):
        if not error:
            return None
        if isinstance(error, dict):
            error_type = error.get('type')
            error_message = error.get('message')
        else:
            error_type = type(error).__name__
            error_message = str(error)
        return {
            'type': error_type or 'Error',
            'message': error_message or '',
        }

    def build_details(
        self,
        phase=None,
        step=None,
        tool_name=None,
        arguments=None,
        raw_arguments=None,
        observation=None,
        status=None,
        forced=False,
        error=None,
        prompt=None,
    ):
        if not self.enabled:
            return ''

        details = {}
        if phase:
            details['phase'] = phase
        if step is not None:
            details['step'] = step + 1 if isinstance(step, int) else step
        if tool_name:
            details['tool_name'] = tool_name
        if status:
            details['status'] = status
        if forced:
            details['forced'] = True
        if THOUGHT_PROCESS_RECORD_TOOL_DETAILS and raw_arguments not in (None, ''):
            details['raw_arguments'] = self._to_serializable(raw_arguments)
        if isinstance(arguments, dict):
            details['arguments'] = self._to_serializable(arguments)
        if THOUGHT_PROCESS_RECORD_TOOL_DETAILS and prompt:
            details['prompt'] = self._normalize_prompt(prompt)
        if observation not in (None, ''):
            details['observation'] = self._normalize_observation(observation, tool_name=tool_name)
        normalized_error = self._normalize_error(error)
        if normalized_error:
            details['error'] = normalized_error

        if not details:
            return ''
        return json.dumps(details, ensure_ascii=False)
