import re
from pathlib import Path

import yaml

from seaqa_ai.agent.skills.spec import AgentSkillSpec, SkillPhaseSpec


class SkillLoadError(ValueError):
    """Raised when a SKILL.md file is invalid."""


_FRONTMATTER_PATTERN = re.compile(r'^---\s*\n(.*?)\n---\s*\n(.*)$', re.DOTALL)
_TEMPLATE_VARIABLE_PATTERN = re.compile(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}')
_ANALYSIS_HEADER = '## Analysis instructions'
_HANDLING_HEADER = '## Handling instructions'


def _to_str_tuple(values, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise SkillLoadError(f'{field_name} must be a list.')
    normalized = []
    for value in values:
        item = str(value or '').strip()
        if not item:
            continue
        normalized.append(item)
    if not normalized:
        raise SkillLoadError(f'{field_name} cannot be empty.')
    return tuple(normalized)


def _parse_frontmatter(content: str, skill_path: Path) -> tuple[dict, str]:
    match = _FRONTMATTER_PATTERN.match(content)
    if not match:
        raise SkillLoadError(f'{skill_path} missing YAML frontmatter.')
    frontmatter_text, markdown_body = match.group(1), match.group(2)
    try:
        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except Exception as e:  # pragma: no cover - parser error detail path
        raise SkillLoadError(f'Invalid YAML in {skill_path}: {e}') from e
    if not isinstance(frontmatter, dict):
        raise SkillLoadError(f'{skill_path} frontmatter must be a mapping.')
    return frontmatter, markdown_body


def _extract_section(body: str, header: str, next_header: str | None = None) -> str:
    header_index = body.find(header)
    if header_index < 0:
        raise SkillLoadError(f'Missing section: {header}')
    start_index = header_index + len(header)
    if next_header:
        end_index = body.find(next_header, start_index)
        section = body[start_index:end_index if end_index >= 0 else None]
    else:
        section = body[start_index:]
    section = section.strip()
    if not section:
        raise SkillLoadError(f'Section {header} cannot be empty.')
    return section


def _render_template(
    content: str,
    variables: dict[str, object],
    skill_path: Path,
) -> str:
    def replace(match: re.Match) -> str:
        variable_name = match.group(1)
        if variable_name not in variables:
            raise SkillLoadError(
                f'{skill_path} contains unknown template variable: {variable_name}'
            )
        return str(variables[variable_name])

    return _TEMPLATE_VARIABLE_PATTERN.sub(replace, content)


def load_skill_spec(skill_file_path: str | Path) -> AgentSkillSpec:
    skill_path = Path(skill_file_path)
    content = skill_path.read_text(encoding='utf-8')
    frontmatter, markdown_body = _parse_frontmatter(content, skill_path)

    skill_id = str(frontmatter.get('name') or '').strip()
    if not skill_id:
        raise SkillLoadError(f'{skill_path} missing name.')

    description = str(frontmatter.get('description') or '').strip()
    source_types = _to_str_tuple(frontmatter.get('source-types', []), 'source-types')
    event_types = _to_str_tuple(frontmatter.get('event-types', []), 'event-types')

    analysis_cfg = frontmatter.get('analysis') or {}
    handling_cfg = frontmatter.get('handling') or {}
    if not isinstance(analysis_cfg, dict):
        raise SkillLoadError(f'{skill_path} analysis must be an object.')
    if not isinstance(handling_cfg, dict):
        raise SkillLoadError(f'{skill_path} handling must be an object.')

    analysis_max_steps = int(analysis_cfg.get('max-steps') or 0)
    handling_max_steps = int(handling_cfg.get('max-steps') or 0)
    template_variables = {
        'analysis_search_budget': max(1, analysis_max_steps - 2),
    }
    markdown_body = _render_template(markdown_body, template_variables, skill_path)

    analysis_prompt = _extract_section(markdown_body, _ANALYSIS_HEADER, _HANDLING_HEADER)
    handling_prompt = _extract_section(markdown_body, _HANDLING_HEADER)

    analysis_tools = _to_str_tuple(analysis_cfg.get('tools', []), 'analysis.tools')
    handling_tools = _to_str_tuple(handling_cfg.get('tools', []), 'handling.tools')

    analysis_spec = SkillPhaseSpec(
        prompt=analysis_prompt,
        tools=analysis_tools,
        terminator=str(analysis_cfg.get('terminator') or '').strip(),
        max_steps=analysis_max_steps,
    )
    handling_spec = SkillPhaseSpec(
        prompt=handling_prompt,
        tools=handling_tools,
        terminator=str(handling_cfg.get('terminator') or '').strip(),
        max_steps=handling_max_steps,
    )
    if not analysis_spec.terminator:
        raise SkillLoadError(f'{skill_path} analysis.terminator is required.')
    if not handling_spec.terminator:
        raise SkillLoadError(f'{skill_path} handling.terminator is required.')
    if analysis_spec.terminator not in analysis_tools:
        raise SkillLoadError(f'{skill_path} analysis.terminator must be in analysis.tools.')
    if handling_spec.terminator not in handling_tools:
        raise SkillLoadError(f'{skill_path} handling.terminator must be in handling.tools.')
    if analysis_spec.max_steps <= 0:
        raise SkillLoadError(f'{skill_path} analysis.max-steps must be > 0.')
    if handling_spec.max_steps <= 0:
        raise SkillLoadError(f'{skill_path} handling.max-steps must be > 0.')

    return AgentSkillSpec(
        id=skill_id,
        description=description,
        source_types=source_types,
        event_types=event_types,
        analysis=analysis_spec,
        handling=handling_spec,
    )
