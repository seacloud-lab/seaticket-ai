# -*- coding: utf-8 -*-
import logging
import json
import pytz
import re
import ast
import string
import hashlib
import urllib.parse
from copy import deepcopy
from datetime import datetime
from smolagents.models import MessageRole
from smolagents.agent_types import AgentText
from seaqa_ai.config import TIME_ZONE, S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET
from seaqa_ai.utils.constants import EXTRA_CONTENTS_PROMPT, ConnectionType
from seaqa_ai.utils.storage import S3Storage

time_zone = pytz.timezone(TIME_ZONE)
logger = logging.getLogger(__name__)

def localize_time(date):
    """
    Transfer non-time-zone date to time-zone date

    date: datetime object
    """
    if isinstance(date, datetime):
        if date.tzinfo is None:
            date = pytz.utc.localize(date)
        return date.astimezone(time_zone)
    return date

def clean_json_markdown(raw_response):
    if not isinstance(raw_response, str) and not isinstance(raw_response, AgentText):
        return raw_response
    if raw_response.strip().startswith('```'):
        lines = raw_response.strip().splitlines()
        if lines[0].startswith("```") and lines[-1] == "```":
            return "\n".join(lines[1:-1])
    return raw_response

def parse_json(json_str):
    """
    Parse a JSON string OR AgentText into a JSON object with error correction
    """
    if isinstance(json_str, str) or isinstance(json_str, AgentText):
        try:
            json_obj = json.loads(json_str)
        except:
            try:
                json_obj = ast.literal_eval(json_str)
            except Exception as e:
                logger.warning(f'Failure to parse to json {e}')
                return json_str
        return json_obj
    return json_str

def parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    else:
        try:
            return response.json()
        except:
            pass

def url_to_filename(url):
    """
    Convert URL to valid filename
    """
    # Remove protocol prefix
    url = re.sub(r'^https?://', '', url)

    # Replace invalid characters
    filename = urllib.parse.quote_plus(url)
    # filename = unquote(urllib.parse.quote_plus(url))

    # Ensure filename doesn't exceed maximum length limit (255 characters)
    if len(filename) > 240:
        # Keep beginning and end, use hash value in the middle
        hash_part = hashlib.md5(url.encode('utf-8')).hexdigest()[:16]
        filename = filename[:110] + '_' + hash_part + '_' + filename[-110:]

    # Add .json extension
    return filename + '.json'

def extract_sources(tool_executor, reference_ids):
    sources_results = tool_executor.tool_shared_vars.get('sources_results', [])
    sources = []
    for index in reference_ids:
        if isinstance(index, int) and index < len(sources_results):
            sources.append(sources_results[index])
    return sources

def object_to_json_str(obj):
    result = obj
    if isinstance(obj, str) or isinstance(obj, AgentText):
        result = parse_json(obj)
    result = convert_to_json_code_block(result)
    return result

def convert_to_json_code_block(obj):
    if not isinstance(obj, str) and not isinstance(obj, AgentText):
        result = '```json\n' + json.dumps(obj, indent=4, ensure_ascii=False) + '\n```'
        return result
    return obj

def combine_extra_contents_to_message(extra_contents, message):
    if extra_contents:
        return EXTRA_CONTENTS_PROMPT + convert_to_json_code_block(extra_contents) + f'\n\n' + message
    return message

def smolagent_steps_to_thought_process(memory_steps):
    """
    # Format output of agent memory_steps

    ## Args
    - memory_steps: steps of SmolAgents memory

    ## Returns
    Json-like object with:
    - task: system prompts & user input
    - context: chat history
    - actions: Action steps with tokens usage
    - final_answer: final answer & with tokens usage
    """

    actions_input_tokens = 0
    actions_output_tokens = 0
    actions_total_tokens = 0
    actions_time_usage = 0

    answer_input_tokens = 0
    answer_output_tokens = 0
    answer_total_tokens = 0
    answer_time_usage = 0

    results = {
        'task': {
            'system_prompt': '',
            'user_input': ''
        },
        'context': [],
        'actions': [],
        'final_answer': {
            'result': '',
            'token_usage': {
                'input_tokens': 0,
                'output_tokens': 0,
                'total_tokens': 0
            },
            'time_usage': 0
        },
        'static': {
            'token_usage': {
                'input_tokens': {
                    'action_steps': 0,
                    'answer_generation': 0,
                    'total': 0
                },
                'output_tokens': {
                    'action_steps': 0,
                    'answer_generation': 0,
                    'total': 0
                },
                'total_tokens': {
                    'action_steps': 0,
                    'answer_generation': 0,
                    'total': 0
                }
            },
            'time_usage': {
                'action_steps': 0,
                'answer_generation': 0,
                'total': 0
            }
        }
    }

    for step in memory_steps:
        # task
        if not results['task']['user_input'] and 'task' in step:
            results['task']['user_input'] = step['task']
        if not results['task']['system_prompt'] and 'model_input_messages' in step:
            """
            step['model_input_messages']: sources messages of inputing (including system_prompt, context and user_input):

            ```json
            [
                // system prompts
                {
                    'role': 'system',
                    'content': [
                        {
                            'type': 'text',
                            'text': 'system_prompt'
                        }
                    ]
                },

                // context
                {
                    'role': 'user' // or 'assistant'
                    'content': [
                        {
                            'type': 'text',
                            'text': 'a history chat message'
                        }
                    ]
                },

                // user input
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'text',
                            'text': 'user input'
                        }
                    ]
                }
            ]
            ```
            """
            
            for msg in step['model_input_messages']:
                if msg.get('role') == MessageRole.SYSTEM:
                    results['task']['system_prompt'] = msg['content'][0]['text']
                    break

        # context    
        if 'context' in step:
            """
            response['context'] = [
                {
                    'date': 'date when user sent the message', #str
                    'user_input': 'user_input', # str
                    'assistant_response': [
                        'response_1', # -> {'answer', 'references'}
                        'response_2',
                        # ...
                    ]
                },
                # ...
            ]
            """
            if step['context']['role'] == 'assistant':
                if not results['context']:
                    results['context'].append({
                        'date': step['context']['date'],
                        'user_input': '',
                        'assistant_response': [{
                            'date': step['context']['date'],
                            'content': step['context']['content']
                        }]
                    })
                else:
                    results['context'][-1]['assistant_response'].append({
                        'date': step['context']['date'],
                        'content': step['context']['content']
                    })
            else:
                results['context'].append({
                    'date': step['context']['date'],
                    'user_input': step['context']['content'],
                    'assistant_response': []
                })

        if (tool_calls := step.get('tool_calls')) or (error := step.get('error', {})):
            # action step

            if not step['is_final_answer'] and not error.get('type') == 'AgentMaxStepsError':
                # common actions

                tks = [{
                        'name': tool_call['function']['name'],
                        'arguments': tool_call['function']['arguments'],
                    }
                    for tool_call in tool_calls
                ]

                actions_input_tokens += step['token_usage']['input_tokens']
                actions_output_tokens += step['token_usage']['output_tokens']
                actions_total_tokens += step['token_usage']['total_tokens']
                actions_time_usage += step['timing']['duration']

                results['actions'].append({
                    'tool_calls': tks,
                    'token_usage': {
                        'input_tokens': step['token_usage']['input_tokens'],
                        'output_tokens': step['token_usage']['output_tokens'],
                        'total_tokens': step['token_usage']['total_tokens'],
                    },
                    'time_usage': step['timing']['duration'],
                    'result': step['action_output'] if error else step['observations']
                })
                if error:
                    results['actions'][-1]['error'] = {
                        'type': error['type'],
                        'message': error['message']
                    }

            else:
                # final answer
                result = step['action_output'] if error else step['observations']
                result = object_to_json_str(result)

                answer_input_tokens = step['token_usage']['input_tokens']
                answer_output_tokens = step['token_usage']['output_tokens']
                answer_total_tokens = step['token_usage']['total_tokens']
                answer_time_usage = step['timing']['duration']

                results['final_answer'] = {
                    'result': result,
                    'token_usage': {
                        'input_tokens': step['token_usage']['input_tokens'],
                        'output_tokens': step['token_usage']['output_tokens'],
                        'total_tokens': step['token_usage']['total_tokens'],
                    },
                    'time_usage': step['timing']['duration'],
                    'reach_max_steps': True if error else False
                }
    
    results['static'] = {
        'token_usage': {
            'input_tokens': {
                'action_steps': actions_input_tokens,
                'answer_generation': answer_input_tokens,
                'total': actions_input_tokens + answer_input_tokens
            },
            'output_tokens': {
                'action_steps': actions_output_tokens,
                'answer_generation': answer_output_tokens,
                'total': actions_output_tokens + answer_output_tokens
            },
            'total_tokens': {
                'action_steps': actions_total_tokens,
                'answer_generation': answer_total_tokens,
                'total': actions_total_tokens + answer_total_tokens
            }
        },
        'time_usage': {
            'action_steps': actions_time_usage,
            'answer_generation': answer_time_usage,
            'total': actions_time_usage + answer_time_usage
        }
    }

    return results


def chunk_text(text, max_tokens=500, overlap_tokens=120):
    if not text:
        return []
    
    import tiktoken
    try:
        encoding = tiktoken.encoding_for_model("text-embedding-3-small")
    except Exception:
        encoding = tiktoken.get_encoding("cl100k_base")
    
    tokens = encoding.encode(text)
    n = len(tokens)
    stride = max_tokens - overlap_tokens
    
    if stride <= 0:
        raise ValueError("overlap_tokens must be less than max_tokens to avoid infinite loop.")
    
    chunks = []
    start = 0
    while start < n:
        end = min(start + max_tokens, n)
        chunk_tokens = tokens[start:end]
        chunks.append(encoding.decode(chunk_tokens))
        if end == n:
            break
        start += stride
    
    return chunks

def fix_reference_marks(content):
    """
    content: Dict{answer, reference_ids}
    """

    answer = content.get('answer', '')
    reference_ids = content.get('reference_ids', [])
    if not answer:
        return {
            'answer': 'Operations completed',
            'reference_ids': []
        }

    # step 1, detach each term from combined reference marks (e.g., [Reference 1, Reference 2] to [Reference 1][Reference 2])
    pattern = r'\[Reference\s*([^\]]+)\]'
    def replace_match(match):
        content = match.group(1).strip()
        references = [ref.strip() for ref in content.split(',')]
        references = [ref for ref in references if ref]
        return ''.join(f'[Reference {ref}]' for ref in references)

    answer = re.sub(pattern, replace_match, answer)

    # step 2, check each term in reference_ids is valid
    last_char = answer[-1]
    pattern = f"[{re.escape(string.punctuation)}]"
    last_char_is_punctuation = bool(re.match(pattern, last_char))
    if last_char_is_punctuation:
        answer = answer[:-1]
    for i in range(len(reference_ids)):
        if f'[Reference {i + 1}]' not in answer:
            answer += f'[Reference {i + 1}]'
    if last_char_is_punctuation:
        answer += last_char
    
    # step 3, remove redundent [Reference n]
    pattern = re.compile(r'\[Reference (\d+)\]')
    replacer = lambda match: match.group(0) if int(match.group(1)) in range(1, 1 + len(reference_ids)) else ''
    answer = pattern.sub(replacer, answer)

    # step 4, remove invalid reference mark
    pattern = r'\[Reference\s+([^\]]+)\]'
    def replace_match(match):
        ref_content = match.group(1).strip()
        if ref_content.isdigit() and not ref_content.startswith('0'):
            return match.group(0)
        else:
            return ''
    
    answer = re.sub(pattern, replace_match, answer)

    return {
        'answer': answer,
        'reference_ids': reference_ids
    }

class LLMChatCompletionException(Exception):
    pass

def gen_seadb_table_name(connection_type, connection_id):
    if connection_type == ConnectionType.KNOWLEDGE_BASE.value:
        return ConnectionType.KNOWLEDGE_BASE.value
    elif connection_type == ConnectionType.GITHUB_ISSUE.value:
        return f'{connection_type}_{connection_id}', f'{connection_type}_comments_{connection_id}'
    elif connection_type == ConnectionType.DISCOURSE_FORUM.value:
        return f'{connection_type}_{connection_id}', f'{connection_type}_replies_{connection_id}'
    return f'{connection_type}_{connection_id}', ''

def search_records_to_map(search_records, search_results):
    """
    func:  Deduplication and format search_records to a map:
    {
        'connection_id': {
            'record_id': record # 'record_id', i.e., _pk in seadb, _id in seaqa-indexer
        }
    }
    i.e., search_results on other funcs
    """
    connection_id_record_id_record_map = search_results
    candidate_key = ''
    for record in search_records:
        try:
            connection_id = int(record.get('connection_id', 'unknown'))
        except:
            connection_id = record.get('connection_id', 'unknown')
        if not candidate_key:
            if '_id' in record:
                candidate_key = '_id'
            elif '_pk' in record:
                candidate_key = '_pk'
            elif 'pk' in record:
                candidate_key = 'pk'
            else:
                break
        try:
            record_id = int(record[candidate_key])
        except:
            record_id = record[candidate_key]
        if connection_id not in connection_id_record_id_record_map:
            connection_id_record_id_record_map[connection_id] = {}
        connection_id_record_id_record_map[connection_id][record_id] = record
    return connection_id_record_id_record_map

def set_ai_summary(search_results, seadb_api, project_uuid, connection_id_type_map):
    """
    func: set ai_summary field

    Args: 
    search_results: a map of {
        'connection_id': {
            'record_id': record # 'record_id', i.e., _pk in seadb, _id in seaqa-indexer
        }
    }
    connection_id_type_map: a map of {
        'connection_id': 'connection_type'
    }

    search_results also present as return
    """

    for connection_id, record_ids in deepcopy(search_results).items():
        if not record_ids:
            continue
        connection_type = connection_id_type_map[connection_id]
        connection_table_name, _ = gen_seadb_table_name(connection_type, connection_id)
        seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_ids, connection_table_name, record_fields_str='ai_summary')
        for record_id, record_comments_map in seadb_record_id_record_comments_map.items():
            search_results[connection_id][record_id]['ai_summary'] = record_comments_map['record']['ai_summary']

    return search_results

def retrieve_content_of_search_results(search_results, seadb_api, project_uuid, connection_id_type_map):
    """
    func type: output formatter

    Args: 
    search_results: a map of {
        'connection_id': {
            'record_id': record # 'record_id', i.e., _pk in seadb, _id in seaqa-indexer
        }
    }
    connection_id_type_map: a map of {
        'connection_id': 'connection_type'
    }

    search_results also present as return
    """
    for connection_id, record_id_record_map in deepcopy(search_results).items():
        connection_type = connection_id_type_map[connection_id]
        connection_table_name, comments_table_name = gen_seadb_table_name(connection_type, connection_id)
        if connection_type == ConnectionType.SITE.value:
            with S3Storage(S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET) as s3_storage:
                project_uuid_to_s3 = project_uuid.replace('-', '')
                for record_id, record in record_id_record_map.items():
                    search_results[connection_id][record_id]['content'] = s3_storage.read_json(
                        site_id=connection_id,
                        project_uuid=project_uuid_to_s3,
                        filename=url_to_filename(record['url'])
                    )['content']
                    search_results[connection_id][record_id]['content_preview'] = record['ai_summary']
                
        elif connection_type == ConnectionType.SEAFILE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content_preview'] = record['ai_summary']
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']
        elif connection_type == ConnectionType.DISCOURSE_FORUM.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='topic_id', record_fields_str='_pk', comments_field_str='author, post_number, content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['comments'] = [
                    {
                        'author': comment['author'],
                        'post_number': comment['post_number'],
                        'content': comment['content']
                    }
                    for comment in seadb_record_id_record_comments_map[record_id]['comments']
                ]
                search_results[connection_id][record_id]['content'] = convert_to_json_code_block(search_results[connection_id][record_id])
                del search_results[connection_id][record_id]['comments']
                search_results[connection_id][record_id]['content_preview'] = record['ai_summary']
        elif connection_type == ConnectionType.GITHUB_ISSUE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='issue_id', has_deleted_field=True, record_fields_str='content', comments_field_str='author, content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']
                search_results[connection_id][record_id]['comments'] = [
                    {
                        'author': comment['author'],
                        'content': comment['content']
                    }
                    for comment in seadb_record_id_record_comments_map[record_id]['comments']
                ]
                search_results[connection_id][record_id]['content'] = convert_to_json_code_block(search_results[connection_id][record_id])
                del search_results[connection_id][record_id]['comments']
                search_results[connection_id][record_id]['content_preview'] = record['ai_summary']
        elif connection_type == ConnectionType.EMAIL.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content_preview'] = record['ai_summary']
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']

    return search_results
