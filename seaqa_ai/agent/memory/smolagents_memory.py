from smolagents.memory import MemoryStep, AgentMemory
from smolagents.models import ChatMessage, MessageRole

class SmolAgentsMemoryStep(MemoryStep):
    """
    record: dict likes {
        'role': ...,
        'content': ...,
        ... // other properties
    }
    """
    record: dict

    def to_messages(self, *args, **kwagrs):
        result = []
        if self.record['role'] == 'user':
            result = [ChatMessage(role=MessageRole.USER, content=[{
                'date': self.record['created_at'],
                'type': 'text',
                'text': self.record['content']
            }])]
        elif self.record['role'] == 'assistant':
            result = [ChatMessage(role=MessageRole.ASSISTANT, content=[{
                'date': self.record['created_at'],
                'type': 'text',
                'text': self.record['content']
            }])]
        return result

class SmolAgentsMemory(AgentMemory):
    """
    SeaQA - AI History Memory (agent mode)
    """

    def __add__(self, record):
        assert isinstance(record, dict), 'Invalid record type, the type of record has to be a dict'
        
        memory_step = SmolAgentsMemoryStep()
        memory_step.record = record
        self.steps.append(memory_step)
        return self
