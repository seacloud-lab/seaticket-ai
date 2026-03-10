# -*- coding: utf-8 -*-
import logging
import uuid
import json
import pytz
import re
import ast
import hashlib
import uuid
import urllib.parse
from copy import deepcopy
from datetime import datetime, timezone
from seaqa_ai.config import TIME_ZONE, MAX_DOCUMENT_CONTENT_SIZE, \
    S3_HOST, S3_KEY_ID, S3_SECRET_KEY, S3_WEB_CRAWL_BUCKET, THOUGHT_PROCESS_RECORD_TOOL_DETAILS
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType, SOURCES_TYPE, \
    ATTACHMENTS_PROMPTS, ATTACHMENTS_TYPE
from seaqa_ai.utils.storage import S3Storage

time_zone = pytz.timezone(TIME_ZONE)
logger = logging.getLogger(__name__)


def uuid_str_to_36_chars(project_uuid):
    if isinstance(project_uuid, uuid.UUID):
        return str(project_uuid)
    if len(project_uuid) == 32:
        return str(uuid.UUID(project_uuid))
    else:
        return project_uuid

def uuid_str_to_32_chars(project_uuid):
    if len(project_uuid) == 36:
        return uuid.UUID(project_uuid).hex
    return project_uuid

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

def now_iso():
    return localize_time(datetime.now(timezone.utc)).isoformat()

def parse_datetime(dt_str):
    if not dt_str:
        return None
    try:
        if isinstance(dt_str, datetime):
            return localize_time(dt_str)
        if dt_str.endswith('Z'):
            dt_str = dt_str[:-1] + '+00:00'
        return localize_time(datetime.fromisoformat(dt_str))
    except Exception as e:
        logger.exception(f'Error parsing datetime: {e}')
        return None

def clean_json_markdown(raw_response):
    if not isinstance(raw_response, str):
        return raw_response
    if raw_response.strip().startswith('```'):
        lines = raw_response.strip().splitlines()
        if lines[0].startswith("```") and lines[-1] == "```":
            return "\n".join(lines[1:-1])
    return raw_response

def parse_json(json_str):
    """
    Parse a JSON string into a JSON object with error correction
    """
    if isinstance(json_str, str):
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

def gen_completion_format(schema, name='result_parser'):
    return {
        'type': 'json_schema',
        'json_schema': {
            'name': name,
            'strict': True,
            'schema': schema
        }
    }

def remove_sources_content_and_snippets(sources):
    for source in sources:
        source.pop('content', None)
        source.pop('snippets', None)
    return sources

def filter_tool_calls_content(tool_name, results):
    if not THOUGHT_PROCESS_RECORD_TOOL_DETAILS and tool_name in (
            'documents_search', 
            'issues_search'
        ):
        return remove_sources_content_and_snippets(deepcopy(results))
    return results

def object_to_json_str(obj):
    result = obj
    if isinstance(obj, str):
        result = parse_json(obj)
    result = convert_to_json_code_block(result)
    return result

def convert_to_json_code_block(obj):
    if not isinstance(obj, str):
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

def remove_attachments_content(attachments):
    new_attachments = deepcopy(attachments)
    for attachment in new_attachments:
        attachment.pop('content', None)
        attachment.pop('comments', None)
        attachment.pop('emails', None)
    return new_attachments

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

def retrieve_origin_reference_format(content, sources, with_tanger=False):
    """
    Restore the original reference format to avoid context errors.
    """
    if sources:
        if with_tanger:
            content += '\n\n\nThe following information is for context only (not exist in your original answer and is for reference only)!!! Do not output this as part of the formatting in subsequent turns:\n'
        for i, source in enumerate(sources):
            content = content.replace(f'[Reference {i + 1}]', f'<reference_{i}>')
            if with_tanger:
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

    if connection_type == ExtraSourceType.KNOWLEDGE_BASE.value:
        connection_table_name = 'knowledge_base'
    elif connection_type == ExtraSourceType.TICKET.value:
        connection_table_name = 'tickets'
        comments_table_name = 'ticket_comments'
    
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
        if record['type'] == ExtraSourceType.KNOWLEDGE_BASE.value:
            connection_id = ExtraSourceType.KNOWLEDGE_BASE.value
        elif record['type'] == ExtraSourceType.TICKET.value:
            connection_id = ExtraSourceType.TICKET.value
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
        if record['type'] in ExtraSourceType:
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
        # documents
        if connection_type == ExtraSourceType.KNOWLEDGE_BASE.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, record_fields_str='content')
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']
        elif connection_type == ConnectionType.SITE.value:
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

        # issues
        elif connection_type == ExtraSourceType.TICKET.value:
            seadb_record_id_record_comments_map = seadb_api.get_record_and_comments(project_uuid, record_id_record_map.keys(), connection_table_name, comments_table_name=comments_table_name, record_fields_str='content', comments_field_str='creator, content', topic_id_field_name='_pk', comments_topic_id_field_name='ticket_id', has_deleted_field=True)
            for record_id, record in record_id_record_map.items():
                search_results[connection_id][record_id]['content'] = seadb_record_id_record_comments_map[record_id]['record']['content']
                search_results[connection_id][record_id]['comments'] = [
                    {
                        'creator': comment['creator'],
                        'content': comment['content']
                    }
                    for comment in seadb_record_id_record_comments_map[record_id].get('comments', [])
                ]
                search_results[connection_id][record_id]['content'] = deepcopy(search_results[connection_id][record_id])
                del search_results[connection_id][record_id]['comments']
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

def uuid_str_to_32_chars(project_uuid):
    if len(project_uuid) == 36:
        return uuid.UUID(project_uuid).hex
    else:
        return project_uuid
