import json
import re
from sqlalchemy import desc
from datetime import datetime, timedelta
from seaqa_ai.config import THOUGHT_PROCESS_RECORD_USER_INPUT_RAW, THOUGHT_PROCESS_RECORD_USER_INPUT
from seaqa_ai.utils import expand_grouped_reference_markers, remove_reference_markers
from seaqa_ai.utils.memory import OpenAIMemory
from seaqa_ai.utils.record.identity import EXTRA_RECORD_SOURCE_TYPES, validate_record_identity
from seaqa_ai.services.chat_service.utils import combine_attachments_to_message


class ChatMemory(OpenAIMemory):
    _DISPLAY_REFERENCE_RE = re.compile(r'\[Reference ([0-9]+)\]')
    _TOOL_REFERENCE_RE = re.compile(r'<reference_[0-9]+>')

    @classmethod
    def _strip_tool_references(cls, text):
        return cls._TOOL_REFERENCE_RE.sub('', expand_grouped_reference_markers(text))

    @staticmethod
    def _historical_source_identity(source):
        source_type = source.get('type')
        record_id = source.get('_id')
        if not isinstance(source_type, str) or type(record_id) is not int or record_id < 0:
            return None
        connection_id = None if source_type in EXTRA_RECORD_SOURCE_TYPES else source.get('connection_id')
        try:
            validate_record_identity(source_type, connection_id, record_id)
        except ValueError:
            return None
        return {'source_type': source_type, 'connection_id': connection_id, 'record_id': record_id}

    @classmethod
    def _format_historical_answer(cls, content, sources, answer_number):
        """Keep historical source provenance without exposing current-turn citation markers."""
        sources = sources if isinstance(sources, list) else []
        cited_sources = {}

        def replace_reference(match):
            number = int(match.group(1))
            if not 1 <= number <= len(sources) or not isinstance(sources[number - 1], dict):
                return ''
            label = f'H{answer_number}-{number}'
            if label not in cited_sources:
                source = sources[number - 1]
                title = source.get('title')
                item = {'label': label}
                if isinstance(title, str) and title.strip():
                    item['title'] = remove_reference_markers(cls._strip_tool_references(title))
                identity = cls._historical_source_identity(source)
                if identity is not None:
                    item['identity'] = identity
                cited_sources[label] = item
            return f'[Historical source {label}]'

        answer = cls._DISPLAY_REFERENCE_RE.sub(replace_reference, cls._strip_tool_references(content or ''))
        if cited_sources:
            answer += '\n\nHistorical sources for this answer (context only):\n'
            answer += json.dumps(list(cited_sources.values()), ensure_ascii=False)
        return answer

    def __iter__(self):
        answer_number = 0
        for step in self.steps:
            new_step = step
            if 'id' in step: # db record
                role = step['role']
                if role == 'assistant':
                    answer_number += 1
                    content = self._format_historical_answer(step['content'], step.get('sources'), answer_number)
                else:
                    content = combine_attachments_to_message(step['attachments'], step['content'])
                new_step = {'role': role, 'content': content}
            yield new_step

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

def build_memory_from_db(db_session_class, system_prompts, session_uuid, window_limit, valid_time, message_model):
    memory = ChatMemory(system_prompts)
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
