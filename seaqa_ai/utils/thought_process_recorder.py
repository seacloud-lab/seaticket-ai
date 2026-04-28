import logging
from seaqa_ai import config
from seaqa_ai.utils import object_to_json_str

logger = logging.getLogger(__name__)

class ThoughtProcessRecorder(object):
    def __init__(self):
        self.task = {}
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
        
        results ={
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
                    'answer_generation': self.final_answer['time_usage'],
                    'total': self.final_answer['time_usage'] + actions_total_time_usage
                }
            }
        }

        if config.THOUGHT_PROCESS_ENABLE_RECORD_TASKS and (config.THOUGHT_PROCESS_RECORD_SYSTEM_PROMPTS or config.THOUGHT_PROCESS_RECORD_USER_INPUT):
            results['task'] = self.task
            if not config.THOUGHT_PROCESS_RECORD_SYSTEM_PROMPTS:
                del results['task']['system_prompts']
            if not config.THOUGHT_PROCESS_RECORD_USER_INPUT:
                del results['task']['user_input']
            elif not config.THOUGHT_PROCESS_RECORD_USER_INPUT_RAW:
                del results['task']['user_input']['raw']
        if config.THOUGHT_PROCESS_RECORD_CONTEXT and self.context:
            results['context'] = self.context

        return results
