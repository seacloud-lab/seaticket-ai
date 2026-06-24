from seaqa_ai import config
from seaqa_ai.services.skills.research_report import RESEARCH_REPORT_SKILL
from seaqa_ai.services.skills.spec import SkillSpec
from seaqa_ai.utils.tools import SEARCH_TOOL_NAMES


class SkillRegistry:
    _skills = {
        RESEARCH_REPORT_SKILL.id: RESEARCH_REPORT_SKILL,
    }

    @classmethod
    def get(cls, skill_id):
        if not skill_id:
            return None
        return cls._skills.get(skill_id)

    @classmethod
    def has(cls, skill_id):
        return bool(cls.get(skill_id))

    @classmethod
    def all(cls):
        return list(cls._skills.values())

    @classmethod
    def available_tool_names(cls, context=None):
        context = context or {}
        tool_names = set(SEARCH_TOOL_NAMES)
        if not context.get('is_external_portal'):
            tool_names.update({
                'generate_markdown',
                'create_knowledge_base_entry',
            })
        if config.ENABLE_TEST_TOOL:
            tool_names.add('test_tool')
        return tool_names

    @classmethod
    def is_available(cls, skill_id, context=None):
        skill = cls.get(skill_id)
        if skill is None:
            return False

        context = context or {}
        if context.get('is_external_portal') and not skill.supports_external_portal:
            return False

        required_tools = set(skill.required_tools or ())
        return required_tools.issubset(cls.available_tool_names(context))

    @classmethod
    def available_skills(cls, context=None):
        return [
            skill
            for skill in cls.all()
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
