# -*- coding: utf-8 -*-
import logging
import json
import pytz
import re
import ast
import hashlib
import urllib.parse
from copy import deepcopy
from datetime import datetime
from smolagents.models import MessageRole
from smolagents.agent_types import AgentText
from seaqa_ai.config import TIME_ZONE, MAX_DOCUMENT_CONTENT_SIZE, \
    S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET
from seaqa_ai.utils.constants import ConnectionType, NonConnectionType, \
    ATTACHMENTS_PROMPTS, ATTACHMENTS_TYPE, SOURCES_TYPE
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

def combine_attachments_to_message(attachments, message):
    document_attachments = []
    issues_attachments = []

    for attachment in attachments:
        if attachment['type'] in ATTACHMENTS_TYPE.DOCUMENTS.value:
            document_attachments.append(attachment)
        elif attachment['type'] in ATTACHMENTS_TYPE.ISSUES.value:
            issues_attachments.append(attachment)
    
    final_message = ''
    if document_attachments:
        final_message += ATTACHMENTS_PROMPTS.DOCUMENTS.value + convert_to_json_code_block(document_attachments) + '\n\n'

    if issues_attachments:
        final_message += ATTACHMENTS_PROMPTS.ISSUES.value + convert_to_json_code_block(issues_attachments) + '\n\n'

    return final_message + message

def format_thought_process_user_input(attachments, message):
    document_attachments = []
    issues_attachments = []

    for attachment in attachments:
        if attachment['type'] in ATTACHMENTS_TYPE.DOCUMENTS.value:
            document_attachments.append(attachment)
        elif attachment['type'] in ATTACHMENTS_TYPE.ISSUES.value:
            issues_attachments.append(attachment)
    
    final_message = ''
    if document_attachments:
        final_message += ATTACHMENTS_PROMPTS.DOCUMENTS.value + convert_to_json_code_block(document_attachments)

    if issues_attachments:
        final_message += ATTACHMENTS_PROMPTS.ISSUES.value + convert_to_json_code_block(issues_attachments)

    return final_message + message

def smolagent_steps_to_thought_process(memory_steps, message, attachments):
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
            'user_input': {
                'message': '',
                'attachments': []
            },
            'user_raw_message': ''
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

    results['task']['user_input']['message'] = message
    results['task']['user_input']['attachments'] = attachments

    for step in memory_steps:
        # task
        if not results['task']['user_raw_message'] and 'task' in step:
            results['task']['user_raw_message'] = step['task']
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
                    'result': object_to_json_str(step['action_output'] if error else step['observations'])
                })
                if error:
                    results['actions'][-1]['error'] = {
                        'type': error['type'],
                        'message': error['message']
                    }

            else:
                # final answer
                result = step['action_output'] if error else step['observations']
                result = result

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

def retrieve_origin_reference_format(content, sources):
    """
    Restore the original reference format to avoid context errors.
    """
    if sources:
        content += '\n\n\nThe following information is for context only (not exist in your original answer and is for reference only)!!! Do not output this as part of the formatting in subsequent turns:\n'
        for i, source in enumerate(sources):
            content = content.replace(f'[Reference {i + 1}]', f'<reference_{i}>')
            content += f'<reference_{i}>: [{source.get("title", source.get("url"))}]({source.get("url", "#")})\n'

    return content

def clean_markdown_references(content, sources):
    sources_len = len(sources)
    md_blocks = content.split('<seaqa-markdown')
    result = md_blocks[0]
    for md_block in md_blocks[1:]:
        md_block_contents = md_block.split('</seaqa-markdown>')
        if len(md_block_contents) == 1:
            md_content = md_block
            end_fix = ''
        else:
            md_content = md_block_contents[0]
            end_fix = '</seaqa-markdown>'.join(md_block_contents[1:])
        for i in range(sources_len):
            md_content = md_content.replace(f'[Reference {i + 1}]', '')
        result += '<seaqa-markdown' + md_content + '</seaqa-markdown>' + end_fix
    
    for i in reversed(range(sources_len)):
        if f'[Reference {i + 1}]' not in result:
            for j in range(i + 1, len(sources)):
                content = content.replace(f'[Reference {j + 1}]', f'[Reference {j}]')
            del sources[i]

    return result, sources

def get_answer_and_sources(tool_executor, content):
    sources_results = tool_executor.cache.get('sources_results', [])

    # remove redundent <reference_n> and [Reference n] (format error)
    pattern = re.compile(r'\<reference_(\d+)\>')
    replacer = lambda match: match.group(0) if int(match.group(1)) in range(len(sources_results)) else ''
    content = pattern.sub(replacer, content)

    # convert <reference_n> -> [Reference i]
    ## first_position_map -> {first_position: <reference>}
    first_position_map = {}
    for i in range(len(sources_results)):
        position = content.find(f'<reference_{i}>')
        if position != -1:
            first_position_map[position] = f'<reference_{i}>'
    
    ## replaces_map -> {<reference_n>: [Reference i]}
    replaces_map = {
        first_position_map[position]: f'[Reference {i + 1}]'
        for i, position in enumerate(sorted(first_position_map))
    }

    sources = []
    for origin, target in replaces_map.items():
        content = content.replace(origin, target)
        sources_index = int(origin.split('_')[1][:-1])
        sources.append(sources_results[sources_index])
    
    content, sources = clean_markdown_references(content, sources)

    return content, sources

class LLMChatCompletionException(Exception):
    pass

def gen_seadb_table_name(connection_type, connection_id):
    connection_table_name = f'{connection_type}_{connection_id}'
    comments_table_name = ''

    if connection_type == NonConnectionType.KNOWLEDGE_BASE.value:
        return NonConnectionType.KNOWLEDGE_BASE.value
    elif connection_type == NonConnectionType.TICKET.value:
        return NonConnectionType.TICKET.value
    
    if connection_type == ConnectionType.GITHUB_ISSUE.value:
        comments_table_name = f'{connection_type}_comments_{connection_id}'
    elif connection_type == ConnectionType.DISCOURSE_FORUM.value:
        comments_table_name = f'{connection_type}_replies_{connection_id}'
    elif connection_type == ConnectionType.EMAIL.value:
        comments_table_name = f'{connection_type}_thread_{connection_id}'
    return connection_table_name, comments_table_name

def search_records_to_map(search_records, search_results, is_vector_search=False):
    """
    func:  Deduplication and format search_records to a map:
    {
        'connection_id': {
            'record_id': record # 'record_id', i.e., _pk in seadb, _id in seaqa-indexer
        }
    }
    connection_id_type_map: a map of {
        'connection_id': 'connection_type'
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
        if record['type'] in NonConnectionType:
            connection_id = record['type']
        if connection_id not in connection_id_record_id_record_map:
            connection_id_record_id_record_map[connection_id] = {}
        if is_vector_search and record.get('source_type') == 'chunk': # snippet
            try:
                record['snippets'] = connection_id_record_id_record_map[connection_id][record_id]['snippets']
            except:
                record['snippets'] = []
            record['snippets'].append(record['content'])
        elif not is_vector_search and record['type'] in SOURCES_TYPE.DOCUMENTS.value:
            record['keyword_search_content'] = record['content']
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

    for connection_id, record_id_record_map in deepcopy(search_results).items():
        connection_type = connection_id_type_map[connection_id]
        connection_table_name, comments_table_name = gen_seadb_table_name(connection_type, connection_id)
        if connection_type == ConnectionType.EMAIL.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name, topic_id_field_name='thread_id', comments_topic_id_field_name='_pk', record_fields_str='_pk', comments_field_str='ai_summary')
        else:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='ai_summary')
        failure_to_get_ai_summary_record_ids = []
        for record_id in record_id_record_map:
            if record_id in seadb_record_id_record_comments_map:
                if connection_type == ConnectionType.EMAIL.value:
                    thread = seadb_record_id_record_comments_map[record_id].get('comments', [])
                    if len(thread) > 0:
                        thread = thread[0]
                        search_results[connection_id][record_id]['ai_summary'] = thread['ai_summary']
                        continue
                else:
                    search_results[connection_id][record_id]['ai_summary'] = seadb_record_id_record_comments_map[record_id]['record']['ai_summary']
                    continue
            failure_to_get_ai_summary_record_ids.append(record_id)
            # remove records without ai_summary
            del search_results[connection_id][record_id]
            if not search_results[connection_id]:
                # empty connection records
                del search_results[connection_id]
        if failure_to_get_ai_summary_record_ids:
            logger.warning(f'Unable to retrieve ai_summary: connection_id: {connection_id}, record_ids: {failure_to_get_ai_summary_record_ids}')

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
    for connection_id, record_id_record_map in search_results.items():
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

        elif connection_type == ConnectionType.SEAFILE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='content')
            for record_id, record in record_id_record_map.items():
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
                    for comment in seadb_record_id_record_comments_map[record_id].get('comments', [])
                ]
                search_results[connection_id][record_id]['content'] = deepcopy(search_results[connection_id][record_id])
                del search_results[connection_id][record_id]['comments']
        elif connection_type == ConnectionType.GITHUB_ISSUE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, topic_id_field_name='issue_id', record_fields_str='content', comments_field_str='author, content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']
                search_results[connection_id][record_id]['comments'] = [
                    {
                        'author': comment['author'],
                        'content': comment['content']
                    }
                    for comment in seadb_record_id_record_comments_map[record_id].get('comments', [])
                ]
                search_results[connection_id][record_id]['content'] = deepcopy(search_results[connection_id][record_id])
                del search_results[connection_id][record_id]['comments']
        elif connection_type == ConnectionType.EMAIL.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']
    
    for connection_id, record_id_record_map in search_results.items():
        for record_id, record in record_id_record_map.items():
            if record['type'] in SOURCES_TYPE.DOCUMENTS.value:
                if 'keyword_search_content' in record:
                    search_results[connection_id][record_id]['content'] = record['keyword_search_content']
                    del search_results[connection_id][record_id]['keyword_search_content']
                search_results[connection_id][record_id]['content'] = search_results[connection_id][record_id]['content'][:MAX_DOCUMENT_CONTENT_SIZE]

    return search_results
