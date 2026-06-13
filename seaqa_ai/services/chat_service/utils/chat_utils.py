import logging
import time
from seaqa_ai import config
from seaqa_ai.utils import parse_json
from seaqa_ai.utils.sse import SSE
from seaqa_ai.utils.tools import filter_tool_calls_content
from seaqa_ai.services.chat_service.tools.chat_pseudo_tool import ChatPseudoTool
logger = logging.getLogger(__name__)


class BasicChatUtils:
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
        completion_kwargs['input'] = list(memory)
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
    def execute_tool_with_retry(cls, tool_executor, tool_call, call_backer_factory, memory):
        tool_name = tool_call['name']
        arguments = tool_call.get('arguments')
        if isinstance(arguments, str):
            arguments = parse_json(arguments)

        retry_errors = []
        call_backer = call_backer_factory()
        for current_retry in range(config.TOOL_CALL_MAX_RETRIES + 1):
            try:
                raw_output = tool_executor.execute(tool_call, call_backer)
                tool_output = ChatPseudoTool.enrich_tool_output(tool_name, raw_output, tool_executor)
                tool_executor.thought_process.append_last_group_tool_retry(retry_errors)
                tool_executor.thought_process.add_tool_call(tool_name, arguments, call_backer.execution_detail, filter_tool_calls_content(tool_name, raw_output))
                memory += {
                    'type': 'function_call_output',
                    'call_id': tool_call.get('call_id', ''),
                    'output': tool_output
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
                    'type': 'function_call_output',
                    'call_id': tool_call.get('call_id', ''),
                    'output': retry_errors[-1]
                }
                return

class StreamingChatUtils(BasicChatUtils):
    @classmethod
    def execute_tool_with_retry(cls, tool_executor, tool_call, call_backer_factory, memory, current_tool_call_id, total_tool_calls_num):
        tool_name = tool_call['name']
        arguments = tool_call.get('arguments')
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
                raw_output = tool_executor.execute(tool_call, call_backer)
                tool_output = ChatPseudoTool.enrich_tool_output(tool_name, raw_output, tool_executor)
                tool_executor.thought_process.append_last_group_tool_retry(retry_errors)
                tool_executor.thought_process.add_tool_call(tool_name, arguments, call_backer.execution_detail, filter_tool_calls_content(tool_name, raw_output))
                memory += {
                    'type': 'function_call_output',
                    'call_id': tool_call.get('call_id', ''),
                    'output': tool_output
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
                    'type': 'function_call_output',
                    'call_id': tool_call.get('call_id', ''),
                    'output': retry_errors[-1]
                }
                return
