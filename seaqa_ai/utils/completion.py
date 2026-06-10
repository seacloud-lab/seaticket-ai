import logging
import json
import time
from seaqa_ai import config
from seaqa_ai.utils import parse_json
from seaqa_ai.utils.sse import SSE
from seaqa_ai.utils.tools import filter_tool_calls_content
logger = logging.getLogger(__name__)

def gen_completion_format(schema, name='result_parser'):
    return {
        'type': 'json_schema',
        'json_schema': {
            'name': name,
            'strict': True,
            'schema': schema
        }
    }

class BasicCompletionUtils:
    @classmethod
    def build_completion_kwargs(cls, memory, context, tool_executor, current_step, has_inserter_max_step_prompt, max_steps_disabled_prompt):
        completion_kwargs = {
            'context': context,
            'num_retries': 1
        }
        if current_step < config.MAX_STEPS:
            completion_kwargs['tools'] = tool_executor.get()
        else:
            if not has_inserter_max_step_prompt:
                memory += {
                    'role': 'system',
                    'content': max_steps_disabled_prompt
                }
                has_inserter_max_step_prompt = True
            completion_kwargs['tool_choice'] = 'none'
        completion_kwargs['messages'] = memory
        return completion_kwargs, has_inserter_max_step_prompt

    @classmethod
    def build_completion_retry_entry(cls, step_number, current_retry, reason, time_usage, token_usage=None, error=None):
        entry = {
            'step': step_number,
            'retry': current_retry,
            'reason': reason,
            'token_usage': token_usage or {},
            'time_usage': time_usage
        }
        if error is not None:
            entry['error'] = error
        return entry

    @classmethod
    def get_response_token_usage(cls, response):
        try:
            return {
                'input_tokens': response.usage.prompt_tokens,
                'output_tokens': response.usage.completion_tokens,
                'total_tokens': response.usage.total_tokens,
            }
        except Exception:
            return {}

    @classmethod
    def execute_tool_with_retry(cls, tool_executor, tool_call, call_backer_factory, memory):
        tool_name = tool_call['function']['name']
        arguments = tool_call['function']['arguments']
        if isinstance(arguments, str):
            arguments = parse_json(arguments)

        retry_errors = []
        call_backer = call_backer_factory()
        for current_retry in range(config.TOOL_CALL_MAX_RETRIES + 1):
            try:
                tool_output = tool_executor.execute(tool_call, call_backer)
                tool_executor.thought_process.append_last_group_tool_retry(retry_errors)
                tool_executor.thought_process.add_tool_call(tool_name, arguments, call_backer.execution_detail, filter_tool_calls_content(tool_name, tool_output))
                memory += {
                    'role': 'tool',
                    'tool_call_id': tool_call['id'],
                    'content': json.dumps(tool_output)
                }
                return
            except Exception as e:
                logger.exception(f'Execute tool call failure: {e}')
                error_message = f'{type(e).__name__}: {repr(e)}'
                retry_errors.append(error_message)
                if current_retry < config.TOOL_CALL_MAX_RETRIES:
                    time.sleep(config.TOOL_CALL_RETRY_INTERVAL)
                    continue

                tool_executor.thought_process.append_last_group_tool_retry(retry_errors)
                tool_executor.thought_process.add_tool_call(tool_name, arguments, call_backer.execution_detail)
                tool_executor.thought_process.set_last_group_error({
                    'type': 'ToolRetryExceeded',
                    'message': retry_errors[-1]
                })
                memory += {
                    'role': 'tool',
                    'tool_call_id': tool_call['id'],
                    'content': retry_errors[-1]
                }
                return

class StreamingCompletionUtils(BasicCompletionUtils):
    @classmethod
    def build_completion_kwargs(cls, memory, context, tool_executor, current_step, has_inserter_max_step_prompt, max_steps_disabled_prompt):
        completion_kwargs, has_inserter_max_step_prompt = super().build_completion_kwargs(
            memory, context, tool_executor, current_step, has_inserter_max_step_prompt, max_steps_disabled_prompt
        )
        completion_kwargs['stream'] = True
        completion_kwargs['stream_options'] = {"include_usage": True}
        return completion_kwargs, has_inserter_max_step_prompt

    @classmethod
    def safe_model_dump(cls, chunk):
        if hasattr(chunk, 'model_dump'):
            try:
                return chunk.model_dump(exclude_none=True)
            except Exception:
                pass
        if hasattr(chunk, 'dict'):
            try:
                return chunk.dict(exclude_none=True)
            except Exception:
                pass
        return repr(chunk)

    @classmethod
    def ensure_tool_call_slot(cls, tool_calls, tool_call_index, tool_call_id=None):
        if tool_call_index is None:
            tool_call_index = len(tool_calls)

        while len(tool_calls) <= tool_call_index:
            tool_calls.append({
                'id': f'call_{len(tool_calls)}',
                'type': 'function',
                'function': {
                    'name': '',
                    'arguments': ''
                }
            })

        if tool_call_id:
            tool_calls[tool_call_index]['id'] = tool_call_id

        return tool_call_index

    @classmethod
    def collect_delta_content(cls, delta_content):
        if isinstance(delta_content, str):
            return delta_content
        if not isinstance(delta_content, list):
            return ''

        content_parts = []
        for part in delta_content:
            if isinstance(part, dict):
                text = part.get('text')
                if isinstance(text, str):
                    content_parts.append(text)
                    continue
                if isinstance(part.get('type'), str) and part['type'] == 'text':
                    text = part.get('content')
                    if isinstance(text, str):
                        content_parts.append(text)
            elif hasattr(part, 'text') and isinstance(part.text, str):
                content_parts.append(part.text)

        return ''.join(content_parts)
    
    @classmethod
    def execute_tool_with_retry(cls, tool_executor, tool_call, call_backer_factory, memory, current_tool_call_id, total_tool_calls_num):
        tool_name = tool_call['function']['name']
        arguments = tool_call['function']['arguments']
        if isinstance(arguments, str):
            arguments = parse_json(arguments)

        retry_errors = []
        call_backer = call_backer_factory()
        for current_retry in range(config.TOOL_CALL_MAX_RETRIES + 1):
            detail = tool_name
            if total_tool_calls_num > 1:
                detail += f', {current_tool_call_id} of {total_tool_calls_num}'
            if current_retry:
                detail += f', retring {current_retry + 1}'
            yield SSE.status('Calling tools', detail)
            try:
                tool_output = tool_executor.execute(tool_call, call_backer)
                tool_executor.thought_process.append_last_group_tool_retry(retry_errors)
                tool_executor.thought_process.add_tool_call(tool_name, arguments, call_backer.execution_detail, filter_tool_calls_content(tool_name, tool_output))
                memory += {
                    'role': 'tool',
                    'tool_call_id': tool_call['id'],
                    'content': json.dumps(tool_output)
                }
                return
            except Exception as e:
                logger.exception(f'Execute tool call failure: {e}')
                error_message = f'{type(e).__name__}: {repr(e)}'
                retry_errors.append(error_message)
                if current_retry < config.TOOL_CALL_MAX_RETRIES:
                    time.sleep(config.TOOL_CALL_RETRY_INTERVAL)
                    continue

                tool_executor.thought_process.append_last_group_tool_retry(retry_errors)
                tool_executor.thought_process.add_tool_call(tool_name, arguments, call_backer.execution_detail)
                tool_executor.thought_process.set_last_group_error({
                    'type': 'ToolRetryExceeded',
                    'message': retry_errors[-1]
                })
                memory += {
                    'role': 'tool',
                    'tool_call_id': tool_call['id'],
                    'content': retry_errors[-1]
                }
                return
