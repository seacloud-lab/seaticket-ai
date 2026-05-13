from sqlalchemy import desc
from datetime import datetime, timedelta
from seaqa_ai.config import THOUGHT_PROCESS_RECORD_USER_INPUT_RAW, THOUGHT_PROCESS_RECORD_USER_INPUT
from seaqa_ai.services.chat_service.utils import combine_attachments_to_message, retrieve_origin_reference_format

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

    @property
    def context_thought_process(self):
        results = []

        for step in self.steps[1:]:
            if 'created_at' not in step:
                continue
            if step['role'] == 'assistant':
                if not results:
                    results.append({
                        'date': step['created_at'],
                        'user_input': '',
                        'assistant_response': [{
                            'date': step['created_at'],
                            'content': {
                                'answer': step['content'],
                                'sources': step['sources']
                            }
                        }]
                    })
                else:
                    results[-1]['assistant_response'].append({
                        'date': step['created_at'],
                        'content': {
                            'answer': step['content'],
                            'sources': step['sources']
                        }
                    })
            else:
                result_of_a_step = {
                    'date': step['created_at'],
                    'assistant_response': []
                }
                if THOUGHT_PROCESS_RECORD_USER_INPUT:
                    result_of_a_step['user_input'] = {
                        'attachments': step['attachments'],
                        'message': step['content']
                    }
                    if THOUGHT_PROCESS_RECORD_USER_INPUT_RAW:
                        result_of_a_step['user_input']['raw'] = combine_attachments_to_message(step['attachments'], step['content'])
                results.append(result_of_a_step)
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
        for step in self.steps:
            new_step = step
            if 'id' in step: # db record
                new_step = {
                    'role': step['role'],
                    'content': retrieve_origin_reference_format(step['content'], step['sources']) if step['role'] == 'assistant' else combine_attachments_to_message(step['attachments'], step['content'])
                }
            yield new_step

def build_memory_from_db(db_session_class, system_prompts, session_uuid, window_limit, valid_time, message_model):
    memory = OpenAIMemory(system_prompts)
    if not session_uuid:
        return memory

    # context config check
    assert isinstance(window_limit, int), 'Invalid window number of context'
    assert isinstance(valid_time, int), 'Invalid valid time of context'

    # query records for newest
    with db_session_class() as db_session:
        query_args = [
            message_model.session_uuid == session_uuid,
            message_model.as_context == True,
            message_model.role.in_(['user', 'assistant'])
        ]
        if valid_time > 0:
            query_args.append(
                message_model.created_at > datetime.now() - timedelta(hours=valid_time)
            )
        records = (
            db_session.query(message_model)
            .filter(*query_args)
            .order_by(desc(message_model.created_at))
        )

        if window_limit > 0:
            records = records.limit(window_limit)

        records = records.all()

        records.sort(key=lambda x: x.created_at)
        
        for record in records:
            memory += record.to_dict()

    return memory
