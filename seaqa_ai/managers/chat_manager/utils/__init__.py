import logging
import re
from copy import deepcopy
from seaqa_ai.config import THOUGHT_PROCESS_RECORD_TOOL_DETAILS
from seaqa_ai.utils import convert_to_json_code_block
from seaqa_ai.utils.constants import ATTACHMENTS_PROMPTS, ATTACHMENTS_TYPE

logger = logging.getLogger(__name__)

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
