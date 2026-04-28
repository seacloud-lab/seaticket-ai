import logging
import re
from copy import deepcopy
from seaqa_ai.managers.chat_manager.system_prompts import (
    CHAT_CONTENT_GENERATOR_TOOLS_EXAMPLES,
    CHAT_CONTENT_GENERATION_RULES,
    CHAT_CORE_PROMPT,
    CHAT_GLOBAL_TOOL_RULES,
    CHAT_OUTPUT_FORMAT_RULES,
    CHAT_SEARCH_TOOLS_EXAMPLES,
    CHAT_SEARCH_POLICY,
    CHAT_SEARCH_REFERENCE_RULES,
)
from seaqa_ai.utils import convert_to_json_code_block
from seaqa_ai.utils.constants import ATTACHMENTS_PROMPTS, ATTACHMENTS_TYPE

logger = logging.getLogger(__name__)

def build_chat_tool_prompt(is_external_portal=False, skip_tool_examples=False):
    tool_prompt_sections = [
        CHAT_GLOBAL_TOOL_RULES,
        CHAT_SEARCH_POLICY,
        CHAT_SEARCH_REFERENCE_RULES,
    ]

    if not skip_tool_examples:
        tool_prompt_sections.append(CHAT_SEARCH_TOOLS_EXAMPLES)

    if not is_external_portal:
        tool_prompt_sections.append(CHAT_CONTENT_GENERATION_RULES)
        if not skip_tool_examples:
            tool_prompt_sections.append(CHAT_CONTENT_GENERATOR_TOOLS_EXAMPLES)

    return '\n\n'.join(tool_prompt_sections)

def build_chat_system_prompts(project_prompt='', is_external_portal=False, skip_tool_examples=False):
    tool_prompt = build_chat_tool_prompt(
        is_external_portal=is_external_portal,
        skip_tool_examples=skip_tool_examples,
    )

    prompts = [
        {
            'role': 'system',
            'content': CHAT_CORE_PROMPT,
        },
        {
            'role': 'system',
            'content': tool_prompt,
        },
        {
            'role': 'system',
            'content': CHAT_OUTPUT_FORMAT_RULES,
        },
    ]

    if project_prompt and project_prompt.strip():
        prompts.append({
            'role': 'system',
            'content': (
                'Project-specific context and preferences:\n'
                f'{project_prompt}\n\n'
                'Use this context only when it does not conflict with higher-priority system rules.'
            )
        })

    return prompts

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

def strip_content_details_from_attachments(attachments):
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
