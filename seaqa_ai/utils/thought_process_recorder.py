from seaqa_ai import config
from seaqa_ai.utils import object_to_json_str

class ThoughtProcessRecorder(object):
    def __init__(self):
        self.task = {}
        self.actions = []
        self.context = []
        self.final_answer = {}

    def set_task(self, system_prompt, user_raw_input, user_message, attachments):
        self.task = {
            'system_prompt': system_prompt,
            'user_input': {
                'raw': user_raw_input,
                'message': user_message,
                'attachments': attachments
            }
        }

    def add_tool_calls_group(self):
        self.actions.append({
            'tool_calls': [],
            'token_usage': {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0
            },
            'time_usage': 0,
            'result': ''
        })

    def set_last_group_time_usage(self, time_usage):
        self.actions[-1]['time_usage'] = time_usage
    
    def update_last_group_tokens_usage(self, tokens_usage={}, replace=False):
        if replace:
            self.actions[-1]['token_usage']['input_tokens'] = tokens_usage.get('input_tokens', 0)
            self.actions[-1]['token_usage']['output_tokens'] = tokens_usage.get('output_tokens', 0)
            self.actions[-1]['token_usage']['total_tokens'] = tokens_usage.get('total_tokens', 0)
        else:
            self.actions[-1]['token_usage']['input_tokens'] += tokens_usage.get('input_tokens', 0)
            self.actions[-1]['token_usage']['output_tokens'] += tokens_usage.get('output_tokens', 0)
            self.actions[-1]['token_usage']['total_tokens'] += tokens_usage.get('total_tokens', 0)
    
    def set_last_group_error(self, error):
        self.actions[-1]['error'] = {
            'type': error['type'],
            'message': error['message']
        }

    def add_tool_call(self, tool_name, arguments, result=None):
        self.actions[-1]['tool_calls'].append({
            'name': tool_name,
            'arguments': {key: object_to_json_str(value) for key, value in arguments.items()}
        })
        self.actions[-1]['result'] += object_to_json_str(result) + '\n'
    
    def set_final_answer(self, result, reach_max_steps=False, token_usage={}, time_usage=0):
        self.final_answer = {
            'result': result,
            'reach_max_steps': reach_max_steps,
            'token_usage': token_usage,
            'time_usage': time_usage
        }

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
            actions_total_tokens_usage = {
                'input_tokens': actions_total_tokens_usage.get('input_tokens', 0) + action['token_usage'].get('input_tokens', 0),
                'output_tokens': actions_total_tokens_usage.get('output_tokens', 0) + action['token_usage'].get('output_tokens', 0),
                'total_tokens': actions_total_tokens_usage.get('total_tokens', 0) + action['token_usage'].get('total_tokens', 0),
            }
            actions_total_time_usage += action.get('time_usage', 0)
        
        results ={
            'actions': self.actions,
            'final_answer': self.final_answer,
            'static': {
                'token_usage': {
                    'input_tokens': {
                        'action_steps': actions_total_tokens_usage.get('input_tokens', 0),
                        'answer_generation': self.final_answer['token_usage'].get('input_tokens', 0),
                        'total': self.final_answer['token_usage'].get('input_tokens', 0) + actions_total_tokens_usage.get('input_tokens', 0)
                    },
                    'output_tokens': {
                        'action_steps': actions_total_tokens_usage.get('output_tokens', 0),
                        'answer_generation': self.final_answer['token_usage'].get('output_tokens', 0),
                        'total': self.final_answer['token_usage'].get('output_tokens', 0) + actions_total_tokens_usage.get('output_tokens', 0)
                    },
                    'total_tokens': {
                        'action_steps': actions_total_tokens_usage.get('total_tokens', 0),
                        'answer_generation': self.final_answer['token_usage'].get('total_tokens', 0),
                        'total': self.final_answer['token_usage'].get('total_tokens', 0) + actions_total_tokens_usage.get('total_tokens', 0)
                    }
                },
                'time_usage': {
                    'action_steps': actions_total_time_usage,
                    'answer_generation': self.final_answer['time_usage'],
                    'total': self.final_answer['time_usage'] + actions_total_time_usage
                }
            }
        }

        if config.THOUGHT_PROCESS_ENABLE_RECORD_TASKS and (config.THOUGHT_PROCESS_RECORD_SYSTEM_PROMPT or config.THOUGHT_PROCESS_RECORD_USER_INPUT):
            results['task'] = self.task
            if not config.THOUGHT_PROCESS_RECORD_SYSTEM_PROMPT:
                del results['task']['system_prompt']
            if not config.THOUGHT_PROCESS_RECORD_USER_INPUT:
                del results['task']['user_input']
            elif not config.THOUGHT_PROCESS_RECORD_USER_INPUT_RAW:
                del results['task']['user_input']['raw']
        if config.THOUGHT_PROCESS_RECORD_CONTEXT and self.context:
            results['context'] = self.context

        return results
