import logging
from functools import lru_cache
from pathlib import Path

from seaqa_ai.services.chat_service.skills.parser import parse_skill_markdown, SKILL_NAME_RE
from seaqa_ai.services.chat_service.skills.spec import SkillSpec, SkillSummary


logger = logging.getLogger(__name__)


def _builtin_skills_root():
    return Path(__file__).resolve().parent / 'builtin'


@lru_cache(maxsize=1)
def list_builtin_skill_names():
    root = _builtin_skills_root()
    if not root.exists():
        return ()
    names = []
    for item in root.iterdir():
        if not item.is_dir():
            continue
        if not SKILL_NAME_RE.match(item.name):
            continue
        names.append(item.name)
    return tuple(sorted(names))


def _builtin_skill_file(skill_name):
    if not skill_name or not SKILL_NAME_RE.fullmatch(skill_name):
        return None
    skill_file = _builtin_skills_root() / skill_name / 'SKILL.md'
    return skill_file if skill_file.is_file() else None


def _read_frontmatter(skill_file):
    lines = []
    delimiters = 0
    with skill_file.open(encoding='utf-8') as fp:
        for line in fp:
            lines.append(line)
            if line.lstrip('\ufeff').strip() == '---':
                delimiters += 1
                if delimiters == 2:
                    break
    if delimiters != 2:
        raise ValueError('SKILL.md frontmatter is required.')
    return ''.join(lines)


@lru_cache(maxsize=None)
def load_builtin_skill_summary(skill_name):
    skill_file = _builtin_skill_file(skill_name)
    if skill_file is None:
        return None

    parsed = parse_skill_markdown(_read_frontmatter(skill_file), expected_name=skill_name)
    return SkillSummary(
        id=parsed['name'],
        name=parsed['name'],
        description=parsed['description'],
        required_tools=tuple(parsed['required_tools']),
        support_external_portal=parsed['support_external_portal'],
    )


@lru_cache(maxsize=None)
def load_builtin_skill_content(skill_name):
    skill_file = _builtin_skill_file(skill_name)
    if skill_file is None:
        return None
    return skill_file.read_text(encoding='utf-8')


@lru_cache(maxsize=None)
def load_builtin_skill(skill_name):
    content = load_builtin_skill_content(skill_name)
    if content is None:
        return None
    parsed = parse_skill_markdown(content, expected_name=skill_name)
    return build_skill_spec_from_content(parsed)


@lru_cache(maxsize=1)
def load_builtin_skills():
    skills = []
    for skill_name in list_builtin_skill_names():
        try:
            skill = load_builtin_skill_summary(skill_name)
        except Exception as e:
            logger.exception(f'Failed to load builtin skill {skill_name}: {e}')
            continue
        if skill:
            skills.append(skill)
    return tuple(skills)


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


def serialize_skill_summary(skill):
    return {
        'name': skill.id,
        'description': skill.description,
        'required_tools': list(skill.required_tools),
        'support_external_portal': skill.support_external_portal,
    }


def serialize_skill_spec(skill, content):
    data = serialize_skill_summary(skill)
    data['content'] = content
    return data
