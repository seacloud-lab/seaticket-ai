import logging

from seaqa_ai import config
from seaqa_ai.seadb_models.models import SchemaTables
from seaqa_ai.services.chat_service.skills.loader import load_builtin_skills, build_skill_spec_from_content
from seaqa_ai.services.chat_service.skills.parser import parse_skill_markdown
from seaqa_ai.utils.seadb_api import SeaDBAPI
from seaqa_ai.services.chat_service.skills.spec import SkillSpec
from seaqa_ai.utils.tools import SEARCH_TOOL_NAMES


logger = logging.getLogger(__name__)


class SkillRegistry:
    _seadb_api = SeaDBAPI(config.SEADB_SERVER_URL, config.SEADB_USER, config.SEADB_PASSWORD)

    @classmethod
    def _disabled_builtin_set(cls, context=None):
        context = context or {}
        disabled = context.get('disabled_builtin_skills') or []
        if not isinstance(disabled, list):
            return set()
        return {
            item.strip()
            for item in disabled
            if isinstance(item, str) and item.strip()
        }

    @classmethod
    def _load_builtin_skills(cls, context=None):
        disabled_builtin_set = cls._disabled_builtin_set(context)
        skills = {}
        for item in load_builtin_skills():
            skill_id = item.get('name')
            if not skill_id or skill_id in disabled_builtin_set:
                continue
            spec = SkillSpec(
                id=skill_id,
                name=item.get('name'),
                description=item.get('description') or '',
                system_prompt=item.get('system_prompt') or '',
                required_tools=tuple(item.get('required_tools') or ()),
                support_external_portal=bool(item.get('support_external_portal', False)),
            )
            skills[spec.id] = spec
        return skills

    @classmethod
    def _load_custom_skills(cls, context=None):
        context = context or {}
        project_uuid = context.get('project_uuid')
        if not project_uuid:
            return {}

        table_name = SchemaTables.CHAT_SKILLS.table_name()
        sql = (
            f"SELECT `_pk`, `name`, `content`, `enabled`, `deleted` FROM `{table_name}` "
            "WHERE (`deleted` = False OR `deleted` IS NULL) "
            "AND (`enabled` = True OR `enabled` IS NULL)"
        )
        try:
            rows = cls._seadb_api.query_rows(project_uuid, sql, convert_keys=False).get('results', [])
        except Exception as e:
            logger.warning('Failed to load custom skills for project %s: %s', project_uuid, e)
            return {}

        skills = {}
        for row in rows:
            skill_id = row.get('name')
            content = row.get('content')
            if not isinstance(skill_id, str) or not isinstance(content, str):
                continue

            try:
                parsed = parse_skill_markdown(content, expected_name=skill_id)
            except Exception as e:
                logger.warning('Skip invalid custom skill %s in project %s: %s', skill_id, project_uuid, e)
                continue

            spec = build_skill_spec_from_content(parsed)
            skills[spec.id] = spec
        return skills

    @classmethod
    def _skills_map(cls, context=None):
        skills = cls._load_builtin_skills(context)
        custom_skills = cls._load_custom_skills(context)
        for skill_id, skill in custom_skills.items():
            if skill_id in skills:
                logger.warning('Custom skill %s conflicts with builtin skill, ignored.', skill_id)
                continue
            skills[skill_id] = skill
        return skills

    @classmethod
    def get(cls, skill_id, context=None):
        if not skill_id or not isinstance(skill_id, str):
            return None
        return cls._skills_map(context).get(skill_id)

    @classmethod
    def has(cls, skill_id, context=None):
        return bool(cls.get(skill_id, context))

    @classmethod
    def all(cls, context=None):
        return list(cls._skills_map(context).values())

    @classmethod
    def available_tool_names(cls, context=None):
        context = context or {}
        tool_names = set(SEARCH_TOOL_NAMES)
        if not context.get('is_external_portal'):
            tool_names.update({
                'generate_markdown',
                'create_knowledge_base_entry',
                'create_ticket',
            })
        if config.ENABLE_TEST_TOOL:
            tool_names.add('test_tool')
        return tool_names

    @classmethod
    def is_available(cls, skill_id, context=None):
        skill = cls.get(skill_id, context)
        if skill is None:
            return False

        context = context or {}
        if context.get('is_external_portal') and not skill.support_external_portal:
            return False

        required_tools = set(skill.required_tools or ())
        return required_tools.issubset(cls.available_tool_names(context))

    @classmethod
    def available_skills(cls, context=None):
        return [
            skill
            for skill in cls.all(context)
            if cls.is_available(skill.id, context)
        ]

    @classmethod
    def available_skills_prompt(cls, context=None):
        described = [
            skill
            for skill in cls.available_skills(context)
            if skill.description
        ]
        if not described:
            return ''

        lines = [
            'Skills provide specialized instructions and workflows for specific tasks.',
            'Use the `skill` tool to load a skill when the task matches its description.',
            '<available_skills>',
        ]
        for skill in described:
            lines.extend([
                '  <skill>',
                f'    <name>{skill.id}</name>',
                f'    <description>{skill.description}</description>',
                '  </skill>',
            ])
        lines.append('</available_skills>')
        return '\n'.join(lines)

    @classmethod
    def skill_system_prompt(cls, skill_id, context=None):
        skill = cls.get(skill_id, context)
        if skill is None or not cls.is_available(skill_id, context):
            return ''

        lines = [
            f'The skill "{skill.id}" is active for this request. Use these specialized instructions when deciding how to proceed.',
            f'<skill_content name="{skill.id}">',
            f'# Skill: {skill.name}',
            '',
            skill.system_prompt.strip(),
            '</skill_content>',
        ]
        return '\n'.join(lines)
