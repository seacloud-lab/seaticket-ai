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
from datetime import datetime, timezone
from seaqa_ai.config import TIME_ZONE
from seaqa_ai.utils.constants import ExtraSourceType

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

def build_system_prompt(base_prompt: str, project_prompt: str = '') -> str:
    """Merge project-level prompt with a base system prompt."""
    if not project_prompt or not project_prompt.strip():
        return base_prompt

    return f"""Project Context:
{project_prompt}

---------------

{base_prompt}"""

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

def remove_reference_markers(content):
    return re.sub(r'\[Reference \d+\]', '', content)

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
                result = result.replace(f'[Reference {j + 1}]', f'[Reference {j}]')
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

def normalize_usernames(usernames_raw):
    """Normalize ticket assignees to a list of user IDs/emails."""
    if not usernames_raw:
        return []
    if isinstance(usernames_raw, list):
        return [str(v).strip() for v in usernames_raw if str(v).strip()]
    if not isinstance(usernames_raw, str):
        value = str(usernames_raw).strip()
        return [value] if value else []

    value = usernames_raw.strip()
    if not value:
        return []

    # parse_json: failure keeps raw string; success may be list/dict/etc.
    parsed = parse_json(value)

    if isinstance(parsed, list):
        return [str(v).strip() for v in parsed if str(v).strip()]

    # Fallback for comma/semicolon separated legacy formats.
    split_values = []
    for sep in (',', ';'):
        if sep in value:
            split_values = [v.strip() for v in value.split(sep) if v.strip()]
            break
    if split_values:
        return split_values
    return [value]

def gen_record_hash(source_type, connection_id, record_id):
    if ExtraSourceType.is_valid(source_type):
        return f'{source_type}-{source_type}-{record_id}'
    return f'{source_type}-{connection_id}-{record_id}'

def remove_sources_content_and_snippets(sources):
    for source in sources:
        source.pop('content', None)
        source.pop('snippets', None)
    return sources
