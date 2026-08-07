import re

import yaml


MAX_SKILL_MARKDOWN_SIZE = 128 * 1024
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
SKILL_NAME_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
FRONTMATTER_RE = re.compile(r'^\ufeff?---\s*\n(.*?)\n---\s*(?:\n(.*))?$', re.DOTALL)


def _parse_bool(value, default=True):
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
    raise ValueError('metadata.seaticket-support-external-portal invalid.')


def _parse_required_tools(metadata):
    value = metadata.get('seaticket-required-tools')
    if value is None:
        return []

    if isinstance(value, str):
        return [item.strip() for item in re.split(r'[\s,]+', value) if item.strip()]

    if isinstance(value, (list, tuple)):
        tools = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError('metadata.seaticket-required-tools invalid.')
            tools.append(item.strip())
        return tools

    raise ValueError('metadata.seaticket-required-tools invalid.')


def parse_skill_markdown(content, expected_name=None):
    if not isinstance(content, str):
        raise ValueError('content invalid.')
    if not content.strip():
        raise ValueError('content invalid.')
    if len(content.encode('utf-8')) > MAX_SKILL_MARKDOWN_SIZE:
        raise ValueError('content too large.')

    match = FRONTMATTER_RE.match(content.strip())
    if not match:
        raise ValueError('SKILL.md frontmatter is required.')

    frontmatter_text = match.group(1)
    body = (match.group(2) or '').strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
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

    metadata = frontmatter.get('metadata') or {}
    if not isinstance(metadata, dict):
        raise ValueError('metadata invalid.')

    required_tools = _parse_required_tools(metadata)
    support_external_portal = _parse_bool(
        metadata.get('seaticket-support-external-portal'),
        default=False,
    )

    return {
        'content': content.strip(),
        'frontmatter': frontmatter,
        'body': body,
        'name': name,
        'description': description,
        'metadata': metadata,
        'required_tools': required_tools,
        'support_external_portal': support_external_portal,
    }
