import json
from seaqa_ai.utils import object_to_json_str

class OpenAIMemory:
    """
    SeaQA - AI History Memory (ask mode), iterable
    """
    def __init__(self, system_prompt):
        self.system_prompt = system_prompt
        self.reset_steps()

    def reset_steps(self):
        self.steps = [{
            'role': 'system',
            'content': self.system_prompt
        }]

    @property
    def context_thought_process(self):
        results = {
            'task': {
                'system_prompt': self.steps[0]['content']
            },
            'context': []
        }

        for step in self.steps[1:]:
            if 'created_at' not in step:
                continue
            if step['role'] == 'assistant':
                if not results['context']:
                    results['context'].append({
                        'date': step['created_at'],
                        'user_input': '',
                        'assistant_response': [{
                            'date': step['created_at'],
                            'content': {
                                'answer': step['content'],
                                'references': object_to_json_str(step['sources'])
                            }
                        }]
                    })
                else:
                    results['context'][-1]['assistant_response'].append({
                        'date': step['created_at'],
                        'content': {
                            'answer': step['content'],
                            'references': object_to_json_str(step['sources'])
                        }
                    })
            else:
                results['context'].append({
                    'date': step['created_at'],
                    'user_input': step['content'],
                    'assistant_response': []
                })
        return results

    def __add__(self, steps):
        if isinstance(steps, dict):
            self.steps.append(steps)
        elif isinstance(steps, list):
            for step in steps:
                self += step
        else:
            raise TypeError('steps only receive two types: dict or list')
        return self

    def __iter__(self):
        messages = []
        for step in self.steps:
            new_message = step
            if 'id' in step: # db record
                new_message = {
                    'role': step['role'],
                    'content': json.dumps({
                        'answer': step['content'],
                        'references': step['sources']
                    }) if step['role'] == 'assistant' else step['content']
                }
            messages.append(new_message)
        return messages.__iter__()
