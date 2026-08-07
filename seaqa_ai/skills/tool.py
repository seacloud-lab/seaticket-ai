from seaqa_ai.skills.registry import SkillRegistry
from seaqa_ai.utils.tools import BasicTool


class LoadExtraSkill(BasicTool):
    tool = {
        'type': 'function',
        'name': 'load_extra_skill',
        'description': 'Load an extra skill when the current task matches one of the available skills listed in the system prompt.',
        'parameters': {
            'type': 'object',
            'properties': {
                'name': {
                    'type': 'string',
                    'description': 'The exact skill name from the available skills list.',
                },
            },
            'required': ['name'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, name, context, tool_executor, app=None):
        seadb_service = app.seadb_service if app else None
        skill = SkillRegistry.get_available(seadb_service, name, context)
        if skill is None:
            raise AssertionError(f'Skill "{name}" is not available for this request')

        context['skill_id'] = skill.name
        lines = [
            f'<skill_content name="{skill.name}">',
            f'# Skill: {skill.name}',
            '',
            skill.content,
            '</skill_content>',
        ]
        return {
            'name': skill.name,
            'content': '\n'.join(lines),
        }
