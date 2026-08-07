import logging
import re
from copy import deepcopy
from seaqa_ai.services.chat_service.system_prompts import (
    CHAT_CONTENT_GENERATOR_TOOLS_EXAMPLES,
    CHAT_CONTENT_GENERATION_RULES,
    CHAT_CORE_PROMPT,
    CHAT_GLOBAL_TOOL_RULES,
    CHAT_OUTPUT_FORMAT_RULES,
    CHAT_SEARCH_TOOLS_EXAMPLES,
    CHAT_SEARCH_POLICY,
    CHAT_SEARCH_REFERENCE_RULES,
)
from seaqa_ai.services.chat_service.skills.registry import SkillRegistry
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

def build_chat_system_prompts(project_prompt='', is_external_portal=False, skip_tool_examples=False, context=None):
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

    skills_prompt = SkillRegistry.available_skills_prompt(context or {'is_external_portal': is_external_portal})
    if skills_prompt:
        prompts.append({
            'role': 'system',
            'content': skills_prompt,
        })

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

def extract_skill_command_from_message(message, skill_ids):
    if not isinstance(message, str) or not skill_ids:
        return None, message

    skill_id_pattern = '|'.join(re.escape(skill_id) for skill_id in skill_ids if skill_id)
    if not skill_id_pattern:
        return None, message

    match = re.search(r'^\s*/(?P<skill_id>' + skill_id_pattern + r')(?=\s|$)', message)
    if not match:
        return None, message

    clean_message = message[match.end():].lstrip()
    return match.group('skill_id'), clean_message

def combine_attachments_to_message(attachments, message):
    document_attachments = []
    issues_attachments = []
    task_attachments = []
    page_content_attachments = []

    for attachment in attachments:
        if attachment['type'] in ATTACHMENTS_TYPE.DOCUMENTS.value:
            document_attachments.append(attachment)
        elif attachment['type'] in ATTACHMENTS_TYPE.ISSUES.value:
            issues_attachments.append(attachment)
        elif attachment['type'] in ATTACHMENTS_TYPE.TASK.value:
            task_attachments.append(attachment)
        elif attachment['type'] == ATTACHMENTS_TYPE.PAGE_CONTENT.value:
            page_content_attachments.append(attachment)
    
    final_message = ''
    if document_attachments:
        final_message += ATTACHMENTS_PROMPTS.DOCUMENTS.value + convert_to_json_code_block(document_attachments) + '\n\n'

    if issues_attachments:
        final_message += ATTACHMENTS_PROMPTS.ISSUES.value + convert_to_json_code_block(issues_attachments) + '\n\n'

    if task_attachments:
        final_message += ATTACHMENTS_PROMPTS.TASK.value + convert_to_json_code_block(task_attachments) + '\n\n'

    if page_content_attachments:
        final_message += ATTACHMENTS_PROMPTS.PAGE_CONTENT.value + convert_to_json_code_block(page_content_attachments) + '\n\n'

    return final_message + message

def extract_images_from_attachments(attachments):
    return [
        {'name': attachment.get('name'), 'mime_type': attachment.get('mime_type'), 'data': attachment.get('data')}
        for attachment in (attachments or [])
        if isinstance(attachment, dict) and attachment.get('type') == 'image' and attachment.get('mime_type') and attachment.get('data')
    ]

def build_user_message_content(attachments, message):
    """
    Returns the message content:
    - No images: a plain text string.
    - With images: a Responses multimodal parts list (input_text + input_image).
    """
    text = combine_attachments_to_message(attachments, message)
    images = extract_images_from_attachments(attachments)
    if not images:
        return text
    return [
        {'type': 'input_text', 'text': text},
        *[
            {
                'type': 'input_image',
                'image_url': f"data:{img['mime_type']};base64,{img['data']}",
                'detail': 'auto',
            }
            for img in images
        ],
    ]

def strip_content_details_from_attachments(attachments):
    new_attachments = deepcopy(attachments)
    for attachment in new_attachments:
        attachment_type = attachment.get('type', '')
        if attachment_type == 'page_content':
            continue
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
