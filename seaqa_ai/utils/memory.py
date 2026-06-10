class OpenAIMemory:
    """
    SeaQA - AI History Memory (ask mode), iterable
    """
    def __init__(self, system_prompts):
        if isinstance(system_prompts, str):
            system_prompts = [{
                'role': 'system',
                'content': system_prompts
            }]
        self.system_prompts = [dict(prompt) for prompt in system_prompts]
        self.reset_steps()

    def reset_steps(self):
        self.steps = [dict(prompt) for prompt in self.system_prompts]
    
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
        for step in self.steps:
            new_step = step
            if 'id' in step: # db record
                new_step = {
                    'role': step['role'],
                    'content': step['content']
                }
            yield new_step
