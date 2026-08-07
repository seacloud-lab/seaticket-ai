import logging
from functools import lru_cache
from pathlib import Path

from seaqa_ai.skills.parser import (
    SKILL_NAME_RE,
    parse_skill_markdown,
)
from seaqa_ai.skills.spec import SkillSpec, SkillSummary


logger = logging.getLogger(__name__)


def _builtin_skills_root():
    return Path(__file__).resolve().parent / 'builtin'


@lru_cache(maxsize=1)
def _list_builtin_skill_names():
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


@lru_cache(maxsize=None)
def _load_builtin_skill_summary(skill_name):
    skill_file = _builtin_skill_file(skill_name)
    if skill_file is None:
        return None

    parsed = parse_skill_markdown(skill_file.read_text(encoding='utf-8'), expected_name=skill_name)
    return SkillSummary(
        name=parsed['name'],
        description=parsed['description'],
        support_agent=bool(parsed.get('support_agent', False)),
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
def load_builtin_skill_summaries_map():
    summaries = {}
    for skill_name in _list_builtin_skill_names():
        try:
            skill = _load_builtin_skill_summary(skill_name)
        except Exception as e:
            logger.exception(f'Failed to load builtin skill {skill_name}: {e}')
            continue
        if skill:
            summaries[skill.name] = skill
    return summaries


def build_skill_spec_from_content(parsed):
    return SkillSpec(
        name=parsed['name'],
        description=parsed['description'],
        support_agent=bool(parsed.get('support_agent', False)),
        content=parsed.get('body') or '',
    )


def serialize_skill_summary(skill):
    return {
        'name': skill.name,
        'description': skill.description,
        'support_agent': bool(getattr(skill, 'support_agent', False)),
    }


def serialize_skill_spec(skill, content):
    data = serialize_skill_summary(skill)
    data['content'] = content
    return data
