import logging
import json
from seaqa_ai import config
from seaqa_ai.utils import object_to_json_str

logger = logging.getLogger(__name__)


class RunTraceRecorder(object):
    def __init__(self):
        self.task = {}
        self.llm_calls = []
        self.actions = []
        self.context = []
        self.final_answer = {}

    def _sum_token_usage(self, total_usage, token_usage):
        return {
            'input_tokens': total_usage.get('input_tokens', 0) + token_usage.get('input_tokens', 0),
            'output_tokens': total_usage.get('output_tokens', 0) + token_usage.get('output_tokens', 0),
            'total_tokens': total_usage.get('total_tokens', 0) + token_usage.get('total_tokens', 0),
        }

    def _sum_retry_tokens_usage(self, retries):
        retry_tokens_usage = {
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': 0
        }
        for retry in retries or []:
            retry_tokens_usage = self._sum_token_usage(retry_tokens_usage, retry.get('token_usage', {}))
        return retry_tokens_usage

    def set_task(self, system_prompts_messages, user_raw_input, user_message, attachments):
        self.task = {
            'system_prompts': [
                system_prompt_message['content']
                for system_prompt_message in system_prompts_messages
            ],
            'user_input': {
                'raw': user_raw_input,
                'message': user_message,
                'attachments': attachments
            }
        }

    def _to_trace_value(self, value):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, default=str))
        except Exception:
            return str(value)

    def _get_tool_names(self, tools):
        tool_names = []
        for tool in tools or []:
            name = tool.get('function', {}).get('name') if isinstance(tool, dict) else None
            if name:
                tool_names.append(name)
        return tool_names

    def add_llm_call(self, phase, step, messages, tools=None, tool_choice=None, metadata=None):
        self.llm_calls.append({
            'phase': phase,
            'step': step,
            'request': {
                'messages': self._to_trace_value(messages),
                'tools': self._to_trace_value(tools or []),
                'tool_names': self._get_tool_names(tools),
                'tool_choice': self._to_trace_value(tool_choice)
            },
            'response': None,
            'token_usage': {},
            'time_usage': 0,
            'metadata': self._to_trace_value(metadata or {})
        })
        return len(self.llm_calls) - 1

    def set_llm_call_response(self, call_id, response=None, token_usage=None, time_usage=0, error=None):
        if call_id is None or call_id >= len(self.llm_calls):
            return
        self.llm_calls[call_id]['response'] = self._to_trace_value(response)
        self.llm_calls[call_id]['token_usage'] = token_usage or {}
        self.llm_calls[call_id]['time_usage'] = time_usage
        if error is not None:
            self.llm_calls[call_id]['error'] = self._to_trace_value(error)

    def add_tool_calls_group(self):
        self.actions.append({
            'tool_calls': [],
            'completion_retry': [],
            'tool_retry': [],
            'token_usage': {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0
            },
            'time_usage': 0,
            'result': ''
        })

    def set_last_group_time_usage(self, time_usage):
        if not self.actions:
            return
        self.actions[-1]['time_usage'] = time_usage

    def update_last_group_tokens_usage(self, tokens_usage={}, replace=False):
        if not self.actions:
            return
        if replace:
            self.actions[-1]['token_usage']['input_tokens'] = tokens_usage.get('input_tokens', 0)
            self.actions[-1]['token_usage']['output_tokens'] = tokens_usage.get('output_tokens', 0)
            self.actions[-1]['token_usage']['total_tokens'] = tokens_usage.get('total_tokens', 0)
        else:
            self.actions[-1]['token_usage']['input_tokens'] += tokens_usage.get('input_tokens', 0)
            self.actions[-1]['token_usage']['output_tokens'] += tokens_usage.get('output_tokens', 0)
            self.actions[-1]['token_usage']['total_tokens'] += tokens_usage.get('total_tokens', 0)

    def set_last_group_error(self, error):
        if not self.actions:
            return
        self.actions[-1]['error'] = {
            'type': error['type'],
            'message': error['message']
        }

    def set_last_group_completion_retry(self, retries):
        if not self.actions:
            return
        self.actions[-1]['completion_retry'] = retries or []

    def append_last_group_tool_retry(self, errors):
        if not self.actions:
            return
        if not errors:
            return
        self.actions[-1]['tool_retry'].extend(errors)

    def add_tool_call(self, tool_name, arguments, execution_detail, result=None):
        if not self.actions:
            return
        tool_call = {
            'name': tool_name,
            'arguments': {key: object_to_json_str(value) for key, value in arguments.items()}
        }

        self.actions[-1]['tool_calls'].append(tool_call)
        if execution_detail:
            self.actions[-1]['tool_calls'][-1]['execution_detail'] = execution_detail
        self.actions[-1]['result'] += object_to_json_str(result) + '\n'

    def set_final_answer(self, result, reach_max_steps=False, token_usage={}, time_usage=0, retry=None):
        self.final_answer = {
            'result': result,
            'reach_max_steps': reach_max_steps,
            'token_usage': token_usage,
            'time_usage': time_usage
        }
        if retry:
            self.final_answer['retry'] = retry

    def _filter_empty_actions(self):
        new_action = []
        for action in self.actions:
            if not action['tool_calls']:
                if new_action:
                    new_action[-1]['token_usage'] = {
                        'input_tokens': new_action[-1]['token_usage'].get('input_tokens', 0) + action['token_usage'].get('input_tokens', 0),
                        'output_tokens': new_action[-1]['token_usage'].get('output_tokens', 0) + action['token_usage'].get('output_tokens', 0),
                        'total_tokens': new_action[-1]['token_usage'].get('total_tokens', 0) + action['token_usage'].get('total_tokens', 0)
                    }
                continue

    def _migrate_final_answer_from_actions(self):
        for i in reversed(range(len(self.actions))):
            for j in reversed(range(len(self.actions[i]['tool_calls']))):
                if self.actions[i]['tool_calls'][j]['name'] == 'final_answer':
                    if not self.final_answer:
                        self.set_final_answer(self.actions[i]['result'], False, self.actions[i]['token_usage'], self.actions[i]['time_usage'])
                        del self.actions[i]['tool_calls'][j]
                        if not self.actions[i]['tool_calls']:
                            del self.actions[i]
                        break
                    else:
                        self.actions[i]['tool_calls'][j]['name'] = 'final_answer (unexpected)'

    @property
    def details(self):
        self._filter_empty_actions()
        self._migrate_final_answer_from_actions()

        actions_total_tokens_usage = {
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': 0
        }
        actions_total_time_usage = 0

        for action in self.actions:
            actions_total_tokens_usage = self._sum_token_usage(actions_total_tokens_usage, action.get('token_usage', {}))
            actions_total_tokens_usage = self._sum_token_usage(actions_total_tokens_usage, self._sum_retry_tokens_usage(action.get('completion_retry', [])))
            actions_total_time_usage += action.get('time_usage', 0)

        final_answer_tokens_usage = self._sum_token_usage(
            self.final_answer.get('token_usage', {}),
            self._sum_retry_tokens_usage(self.final_answer.get('retry', []))
        )
        final_answer_time_usage = self.final_answer.get('time_usage', 0)

        results = {
            'llm_calls': self.llm_calls,
            'actions': self.actions,
            'final_answer': self.final_answer,
            'static': {
                'token_usage': {
                    'input_tokens': {
                        'action_steps': actions_total_tokens_usage.get('input_tokens', 0),
                        'answer_generation': final_answer_tokens_usage.get('input_tokens', 0),
                        'total': final_answer_tokens_usage.get('input_tokens', 0) + actions_total_tokens_usage.get('input_tokens', 0)
                    },
                    'output_tokens': {
                        'action_steps': actions_total_tokens_usage.get('output_tokens', 0),
                        'answer_generation': final_answer_tokens_usage.get('output_tokens', 0),
                        'total': final_answer_tokens_usage.get('output_tokens', 0) + actions_total_tokens_usage.get('output_tokens', 0)
                    },
                    'total_tokens': {
                        'action_steps': actions_total_tokens_usage.get('total_tokens', 0),
                        'answer_generation': final_answer_tokens_usage.get('total_tokens', 0),
                        'total': final_answer_tokens_usage.get('total_tokens', 0) + actions_total_tokens_usage.get('total_tokens', 0)
                    }
                },
                'time_usage': {
                    'action_steps': actions_total_time_usage,
                    'answer_generation': final_answer_time_usage,
                    'total': final_answer_time_usage + actions_total_time_usage
                }
            }
        }

        if config.RUN_TRACE_ENABLE_RECORD_TASKS and (config.RUN_TRACE_RECORD_SYSTEM_PROMPT or config.RUN_TRACE_RECORD_USER_INPUT):
            results['task'] = self.task
            if not config.RUN_TRACE_RECORD_SYSTEM_PROMPT:
                del results['task']['system_prompt']
            if not config.RUN_TRACE_RECORD_USER_INPUT:
                del results['task']['user_input']
            elif not config.RUN_TRACE_RECORD_USER_INPUT_RAW:
                del results['task']['user_input']['raw']
        if config.RUN_TRACE_RECORD_CONTEXT and self.context:
            results['context'] = self.context

        return results
