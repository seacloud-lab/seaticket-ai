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
            self.steps.append(steps)
        elif isinstance(steps, list):
            self.steps += steps
        else:
            raise TypeError('steps only receive two types: dict or list')
        return self

    def __iter__(self):
        return self.steps.__iter__()
