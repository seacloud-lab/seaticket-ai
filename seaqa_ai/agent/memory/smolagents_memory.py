from smolagents.memory import MemoryStep, AgentMemory
from smolagents.models import ChatMessage, MessageRole
from seaqa_ai.utils import object_to_json_str, combine_attachments_to_message, retrieve_origin_reference_format

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
                'text': combine_attachments_to_message(self.record['attachments'], self.record['content'])
            }])]
        elif self.record['role'] == 'assistant':
            result = [ChatMessage(role=MessageRole.ASSISTANT, content=[{
                'date': self.record['created_at'],
                'type': 'text',
                'text': retrieve_origin_reference_format(self.record['content'], self.record['sources'])
            }])]
        return result

    def dict(self):
        """
        To SmolAgents Output
        """
        return {
            'context':{
                'role': self.record['role'],
                'content':  {
                    'answer': self.record['content'],
                    'references': object_to_json_str(self.record['sources']),
                } if self.record['role'] == 'assistant' else combine_attachments_to_message(self.record['attachments'], self.record['content']),
                'date': self.record['created_at']
            }
        }

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
    
    @property
    def context_thought_process(self):
        results = []

        for step in self.steps[1:]:
            if 'created_at' not in step.record:
                continue
            if step.record['role'] == 'assistant':
                if not results:
                    results.append({
                        'date': step.record['created_at'],
                        'user_input': '',
                        'assistant_response': [{
                            'date': step.record['created_at'],
                            'content': {
                                'answer': step.record['content'],
                                'references': object_to_json_str(step.record['sources'])
                            }
                        }]
                    })
                else:
                    results[-1]['assistant_response'].append({
                        'date': step.record['created_at'],
                        'content': {
                            'answer': step.record['content'],
                            'references': object_to_json_str(step.record['sources'])
                        }
                    })
            else:
                results.append({
                    'date': step.record['created_at'],
                    'user_input': combine_attachments_to_message(step.record['attachments'], step.record['content']),
                    'assistant_response': []
                })
        return results
