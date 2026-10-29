import logging

from seaqa_ai.seadb_models.models import SchemaTables
from seaqa_ai.skills.loader import (
    build_skill_spec_from_content,
    load_builtin_skill,
    load_builtin_skill_summaries_map,
)
from seaqa_ai.skills.parser import parse_skill_markdown
from seaqa_ai.skills.scenario import (
    AGENT_SCENARIO_CONTEXT_KEY,
    SkillScenario,
    SkillScenarioKind,
)
from seaqa_ai.skills.spec import SkillSummary
from seaqa_ai import config
from seaqa_ai.utils.tools import SEARCH_TOOL_NAMES

logger = logging.getLogger(__name__)
SKILL_SUMMARIES_CONTEXT_KEY = '_skill_summaries'

class SkillRegistry:
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
            skill_id: skill
            for skill_id, skill in load_builtin_skill_summaries_map().items()
            if skill_id not in disabled_builtin_set
        }

    @classmethod
    def _load_custom_summaries(cls, seadb_service, context=None):
        context = context or {}
        project_uuid = context.get('project_uuid')
        if not project_uuid or not seadb_service:
            return {}

        table_name = SchemaTables.SKILLS.table_name()
        sql = (
            f"SELECT `name`, `description`, `support_agent` "
            f"FROM `{table_name}` WHERE `deleted` = False AND `enabled` = True"
        )
        try:
            rows = seadb_service.query_rows(
                project_uuid, sql, convert_keys=False,
            ).get('results', [])
        except Exception as e:
            logger.warning('Failed to load custom skills for project %s: %s', project_uuid, e)
            return {}

        skills = {}
        for row in rows:
            try:
                skill_id = row['name']
                description = row['description']
                if not isinstance(skill_id, str) or not isinstance(description, str):
                    raise ValueError('Stored skill identity invalid.')
                skill = SkillSummary(
                    name=skill_id,
                    description=description,
                    support_agent=bool(row.get('support_agent', False)),
                )
            except Exception as e:
                logger.warning('Skip invalid custom skill summary in project %s: %s', project_uuid, e)
                continue
            skills[skill.name] = skill
        return skills

    @classmethod
    def _load_custom_skill(cls, seadb_service, skill_id, context=None):
        context = context or {}
        project_uuid = context.get('project_uuid')
        if not project_uuid or not seadb_service:
            return None

        table_name = SchemaTables.SKILLS.table_name()
        sql = (
            f"SELECT `content` FROM `{table_name}` WHERE `name` = '{skill_id}' "
            "AND `deleted` = False AND `enabled` = True LIMIT 1"
        )
        try:
            rows = seadb_service.query_rows(
                project_uuid, sql, convert_keys=False,
            ).get('results', [])
            if not rows:
                return None
            parsed = parse_skill_markdown(rows[0]['content'], expected_name=skill_id)
            return build_skill_spec_from_content(parsed)
        except Exception as e:
            logger.warning('Failed to load custom skill %s for project %s: %s', skill_id, project_uuid, e)
            return None

    @classmethod
    def _skill_summaries_map(cls, seadb_service, context=None):
        if context is not None:
            cached = context.get(SKILL_SUMMARIES_CONTEXT_KEY)
            if cached is not None:
                return cached

        summaries = cls._load_builtin_summaries(context)
        custom_summaries = cls._load_custom_summaries(seadb_service, context)
        for skill_id, skill in custom_summaries.items():
            if skill_id in summaries:
                logger.warning('Custom skill %s conflicts with builtin skill, ignored.', skill_id)
                continue
            summaries[skill_id] = skill

        if context is not None:
            context[SKILL_SUMMARIES_CONTEXT_KEY] = summaries
        return summaries

    @classmethod
    def _is_skill_available(cls, skill, scenario: SkillScenario):
        if scenario.kind is SkillScenarioKind.AGENT:
            return bool(getattr(skill, 'support_agent', False))
        return True

    @classmethod
    def get_available(cls, seadb_service, skill_id, context=None):
        if not skill_id or not isinstance(skill_id, str):
            return None
        scenario = SkillScenario.from_context(context)
        summary = cls._skill_summaries_map(seadb_service, context).get(skill_id)
        if summary is None or not cls._is_skill_available(summary, scenario):
            return None

        skill = load_builtin_skill(skill_id) or cls._load_custom_skill(seadb_service, skill_id, context)
        if skill is None or not cls._is_skill_available(skill, scenario):
            return None
        return skill

    @classmethod
    def available_skills(cls, seadb_service, context=None):
        scenario = SkillScenario.from_context(context)
        return [
            skill
            for skill in cls._skill_summaries_map(seadb_service, context).values()
            if cls._is_skill_available(skill, scenario)
        ]

    @classmethod
    def available_skills_prompt(cls, seadb_service, context=None):
        described = [
            skill
            for skill in cls.available_skills(seadb_service, context)
            if skill.description
        ]
        if not described:
            return ''

        scenario = SkillScenario.from_context(context)
        is_chat = scenario.kind is SkillScenarioKind.CHAT
        intro = (
            'Skills provide specialized instructions and workflows for specific tasks.'
            if is_chat
            else (
                'Skills provide specialized instructions and workflows for the current phase. '
                'The phase output contract and event skill rules have higher priority than loaded extra skills.'
            )
        )

        lines = [
            intro,
            'Use the `load_extra_skill` tool to load a skill when the task matches its description.',
            '',
            'When to load a skill:',
            '- The current task clearly matches one skill description, including any restrictions stated in that description.',
            '- Load the skill before doing the work, so its instructions can shape the whole approach.',
            '- Pass the exact skill name from the list below.',
            '',
            'When not to load a skill:',
            '- No skill description clearly matches the task. Proceed normally instead of forcing the closest one.',
            '- The task is simple enough to complete well without specialized instructions.',
            '- You only want to see what a skill contains. Never load a skill to explore it.',
            '- A skill is already active for this request. Do not reload it or switch to another skill mid-task.',
            '',
            'After loading a skill:',
            '- Follow its instructions for the rest of the task, including its tool policy and output requirements.',
            (
                '- Skill instructions never override the global tool rules, output format rules, or citation rules above.'
                if is_chat
                else '- Skill instructions never override the phase output contract, the event skill, or the final output schema.'
            ),
            '- Loading a skill is not an answer by itself. Continue the task in the same request.',
            '- Do not tell the user that a skill was loaded and do not quote the skill instructions back to them.',
            '',
            '<available_skills>',
        ]
        for skill in described:
            lines.extend([
                '  <skill>',
                f'    <name>{skill.name}</name>',
                f'    <description>{skill.description}</description>',
                '  </skill>',
            ])
        lines.append('</available_skills>')
        return '\n'.join(lines)

    @classmethod
    def engage_agent_skills(cls, seadb_service, context):
        context = context or {}
        context[AGENT_SCENARIO_CONTEXT_KEY] = True
        return cls.available_skills_prompt(seadb_service, context)

    @classmethod
    def available_tool_names(cls, context=None):
        context = context or {}
        tool_names = set(SEARCH_TOOL_NAMES)
        tool_names.add('get_records')
        if not context.get('is_external_portal'):
            tool_names.update({
                'generate_markdown',
                'create_knowledge_base_entry',
                'create_ticket',
            })
        else:
            tool_names.add('generate_markdown')
        if config.ENABLE_TEST_TOOL:
            tool_names.add('test_tool')
        return tool_names
