import json

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

    def __add__(self, steps):
        if isinstance(steps, dict):
            if 'sources' in steps and steps['role'] == 'assistant':
                steps['content'] = json.dumps({
                    'answer': steps['content'],
                    'sources': steps['sources']
                })
            self.steps.append(steps)
        elif isinstance(steps, list):
            for step in steps:
                self += step
        else:
            raise TypeError('steps only receive two types: dict or list')
        return self

    def __iter__(self):
        return self.steps.__iter__()
