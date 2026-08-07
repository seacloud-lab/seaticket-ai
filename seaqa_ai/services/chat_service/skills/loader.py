import logging
from pathlib import Path

from seaqa_ai.services.chat_service.skills.parser import parse_skill_markdown, SKILL_NAME_RE
from seaqa_ai.services.chat_service.skills.spec import SkillSpec


logger = logging.getLogger(__name__)


def _builtin_skills_root():
    return Path(__file__).resolve().parent / 'builtin'


def list_builtin_skill_names():
    root = _builtin_skills_root()
    if not root.exists():
        return []
    names = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        if not SKILL_NAME_RE.match(item.name):
            continue
        names.append(item.name)
    return sorted(names)


def load_builtin_skill(skill_name):
    if not skill_name or not SKILL_NAME_RE.match(skill_name):
        return None

    root = _builtin_skills_root()
    skill_file = root / skill_name / 'SKILL.md'
    if not skill_file.exists():
        return None

    content = skill_file.read_text(encoding='utf-8')
    parsed = parse_skill_markdown(content, expected_name=skill_name)
    return {
        'name': parsed['name'],
        'description': parsed['description'],
        'content': parsed['content'],
        'system_prompt': parsed['body'],
        'required_tools': parsed['required_tools'],
        'support_external_portal': parsed['support_external_portal'],
    }


def load_builtin_skills():
    skills = []
    for skill_name in list_builtin_skill_names():
        try:
            skill = load_builtin_skill(skill_name)
        except Exception as e:
            logger.exception(f'Failed to load builtin skill {skill_name}: {e}')
            continue
        if skill:
            skills.append(skill)
    return skills


def build_skill_spec_from_content(parsed):
    body = parsed.get('body') or ''
    return SkillSpec(
        id=parsed['name'],
        name=parsed['name'],
        description=parsed['description'],
        system_prompt=body,
        required_tools=tuple(parsed.get('required_tools') or ()),
        support_external_portal=bool(parsed.get('support_external_portal', False)),
    )
