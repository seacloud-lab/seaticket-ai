import re

import yaml


MAX_SKILL_MARKDOWN_SIZE = 128 * 1024
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
SKILL_NAME_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
SKILL_HEADER_SEPARATOR = '===================='


def _parse_bool(value, default=False, key_name='support_agent'):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('1', 'true', 'yes', 'on'):
            return True
        if normalized in ('0', 'false', 'no', 'off'):
            return False
    raise ValueError(f'{key_name} invalid.')


def split_skill_document(content):
    if not isinstance(content, str):
        raise ValueError('content invalid.')
    normalized = content.replace('\r\n', '\n').replace('\r', '\n').replace('\ufeff', '', 1)
    header, separator, body = normalized.partition(SKILL_HEADER_SEPARATOR)
    if not separator:
        raise ValueError('SKILL.md frontmatter is required.')
    return header.strip(), body.strip()


def parse_skill_markdown(content, expected_name=None):
    if not isinstance(content, str):
        raise ValueError('content invalid.')
    if not content.strip():
        raise ValueError('content invalid.')
    if len(content.encode('utf-8')) > MAX_SKILL_MARKDOWN_SIZE:
        raise ValueError('content too large.')

    header_text, body = split_skill_document(content)

    try:
        frontmatter = yaml.safe_load(header_text) or {}
    except Exception:
        raise ValueError('SKILL.md frontmatter invalid.')

    if not isinstance(frontmatter, dict):
        raise ValueError('SKILL.md frontmatter invalid.')

    name = frontmatter.get('name')
    if not isinstance(name, str):
        raise ValueError('name invalid.')
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH or not SKILL_NAME_RE.match(name):
        raise ValueError('name invalid.')
    if expected_name and name != expected_name:
        raise ValueError('name does not match target skill.')

    description = frontmatter.get('description')
    if not isinstance(description, str):
        raise ValueError('description invalid.')
    description = description.strip()
    if not description or len(description) > MAX_DESCRIPTION_LENGTH:
        raise ValueError('description invalid.')

    support_agent = _parse_bool(frontmatter.get('support_agent'), default=False, key_name='support_agent')

    return {
        'content': content.strip(),
        'frontmatter': frontmatter,
        'body': body,
        'name': name,
        'description': description,
        'support_agent': support_agent,
    }
