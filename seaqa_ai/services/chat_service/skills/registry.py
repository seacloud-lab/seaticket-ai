import json
import logging

from seaqa_ai import config
from seaqa_ai.seadb_models.models import SchemaTables
from seaqa_ai.services.chat_service.skills.loader import (
    build_skill_spec_from_content,
    load_builtin_skill,
    load_builtin_skills,
)
from seaqa_ai.services.chat_service.skills.parser import parse_skill_markdown
from seaqa_ai.services.chat_service.skills.spec import SkillSummary
from seaqa_ai.utils.seadb_api import SeaDBAPI
from seaqa_ai.utils.tools import SEARCH_TOOL_NAMES


logger = logging.getLogger(__name__)
SKILL_SUMMARIES_CONTEXT_KEY = '_skill_summaries'


def _decode_required_tools(value):
    tools = json.loads(value)
    if not isinstance(tools, list) or not all(isinstance(tool, str) and tool for tool in tools):
        raise ValueError('Stored required_tools invalid.')
    return tuple(tools)


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
    def _load_builtin_summaries(cls, context=None):
        disabled_builtin_set = cls._disabled_builtin_set(context)
        return {
            skill.id: skill
            for skill in load_builtin_skills()
            if skill.id not in disabled_builtin_set
        }

    @classmethod
    def _load_custom_summaries(cls, context=None):
        context = context or {}
        project_uuid = context.get('project_uuid')
        if not project_uuid:
            return {}

        table_name = SchemaTables.CHAT_SKILLS.table_name()
        sql = (
            f"SELECT `name`, `description`, `required_tools`, `support_external_portal` "
            f"FROM `{table_name}` WHERE `deleted` = False AND `enabled` = True"
        )
        try:
            rows = cls._seadb_api.query_rows(project_uuid, sql, convert_keys=False).get('results', [])
        except Exception as e:
            logger.warning('Failed to load custom skills for project %s: %s', project_uuid, e)
            return {}

        skills = {}
        for row in rows:
            try:
                skill_id = row['name']
                description = row['description']
                support_external_portal = row['support_external_portal']
                if not isinstance(skill_id, str) or not isinstance(description, str):
                    raise ValueError('Stored skill identity invalid.')
                if not isinstance(support_external_portal, bool):
                    raise ValueError('Stored support_external_portal invalid.')
                skill = SkillSummary(
                    id=skill_id,
                    name=skill_id,
                    description=description,
                    required_tools=_decode_required_tools(row['required_tools']),
                    support_external_portal=support_external_portal,
                )
            except Exception as e:
                logger.warning('Skip invalid custom skill summary in project %s: %s', project_uuid, e)
                continue
            skills[skill.id] = skill
        return skills

    @classmethod
    def _load_custom_skill(cls, skill_id, context=None):
        context = context or {}
        project_uuid = context.get('project_uuid')
        if not project_uuid:
            return None

        table_name = SchemaTables.CHAT_SKILLS.table_name()
        sql = (
            f"SELECT `content` FROM `{table_name}` WHERE `name` = '{skill_id}' "
            "AND `deleted` = False AND `enabled` = True LIMIT 1"
        )
        try:
            rows = cls._seadb_api.query_rows(project_uuid, sql, convert_keys=False).get('results', [])
            if not rows:
                return None
            parsed = parse_skill_markdown(rows[0]['content'], expected_name=skill_id)
            return build_skill_spec_from_content(parsed)
        except Exception as e:
            logger.warning('Failed to load custom skill %s for project %s: %s', skill_id, project_uuid, e)
            return None

    @classmethod
    def _skill_summaries_map(cls, context=None):
        if context is not None:
            cached = context.get(SKILL_SUMMARIES_CONTEXT_KEY)
            if cached is not None:
                return cached

        summaries = cls._load_builtin_summaries(context)
        custom_summaries = cls._load_custom_summaries(context)
        for skill_id, skill in custom_summaries.items():
            if skill_id in summaries:
                logger.warning('Custom skill %s conflicts with builtin skill, ignored.', skill_id)
                continue
            summaries[skill_id] = skill

        if context is not None:
            context[SKILL_SUMMARIES_CONTEXT_KEY] = summaries
        return summaries

    @classmethod
    def get_available(cls, skill_id, context=None):
        if not skill_id or not isinstance(skill_id, str):
            return None
        summary = cls._skill_summaries_map(context).get(skill_id)
        if summary is None or not cls._is_skill_available(summary, context):
            return None

        skill = load_builtin_skill(skill_id) or cls._load_custom_skill(skill_id, context)
        if skill is None or not cls._is_skill_available(skill, context):
            return None
        return skill

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
    def _is_skill_available(cls, skill, context=None):
        context = context or {}
        if context.get('is_external_portal') and not skill.support_external_portal:
            return False

        required_tools = set(skill.required_tools or ())
        return required_tools.issubset(cls.available_tool_names(context))

    @classmethod
    def available_skills(cls, context=None):
        return [
            skill
            for skill in cls._skill_summaries_map(context).values()
            if cls._is_skill_available(skill, context)
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
        skill = cls.get_available(skill_id, context)
        if skill is None:
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
