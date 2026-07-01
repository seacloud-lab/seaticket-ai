from seaqa_ai.services.chat_service.skills.registry import SkillRegistry
from seaqa_ai.utils.tools import BasicTool


class SkillTool(BasicTool):
    tool = {
        'type': 'function',
        'name': 'skill',
        'description': 'Load a specialized skill when the current task matches one of the available skills listed in the system prompt.',
        'parameters': {
            'type': 'object',
            'properties': {
                'name': {
                    'type': 'string',
                    'description': 'The skill id from the available skills list.'
                },
            },
            'required': ['name'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, name, context, tool_executor):
        skill = SkillRegistry.get(name)
        if skill is None or not SkillRegistry.is_available(name, context):
            raise AssertionError(f'Skill "{name}" is not available for this request')

        context['skill_id'] = skill.id
        lines = [
            f'<skill_content name="{skill.id}">',
            f'# Skill: {skill.name}',
            '',
            skill.system_prompt.strip(),
        ]
        lines.append('</skill_content>')
        return {
            'name': skill.id,
            'content': '\n'.join(lines),
        }
