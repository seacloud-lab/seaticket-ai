import json

from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.utils.constants import AIScenario


class SuggestCreateTicket(BasicAgentSuggestionTool):
    tool = {
        'type': 'function',
        'name': 'suggest_create_ticket',
        'description': 'Create a pending suggestion to create an internal ticket.',
        'parameters': {
            'type': 'object',
            'properties': {'reason': {'type': 'string'}},
            'required': ['reason'],
            'additionalProperties': False,
        },
        'strict': True,
    }

    def execute(self, reason, context=None, app=None):
        reason = str(reason or '').strip()
        if not reason:
            raise ValueError('reason must not be empty')
        context = context or {}
        detail = json.dumps({
            'source_type': context.get('trigger_source_type', context.get('source_type')),
            'source_id': context.get('trigger_source_id', context.get('source_id')),
            'source_title': context.get('trigger_source_title', context.get('source_title')),
            'reason': reason,
            'event': context.get('triggering_event', {}),
            'record': context.get('trigger_record', {}),
        }, ensure_ascii=False, indent=2)
        content = ''
        if app is not None and context.get('project_uuid'):
            draft = app.ai_services.gen_ticket_from_record(detail, {
                'project_uuid': context['project_uuid'],
                'org_id': context.get('org_id', -1),
                'scenario': AIScenario.RECORD_GENERATION.value,
            }) or {}
            priority = draft.get('priority')
            if isinstance(priority, bool) or not isinstance(priority, int):
                priority = 0
            tags = draft.get('tags') if isinstance(draft.get('tags'), list) else []
            content = json.dumps({
                'title': str(
                    draft.get('title')
                    or context.get('trigger_source_title', context.get('source_title'))
                    or ''
                ).strip(),
                'content': str(draft.get('description') or '').strip(),
                'type': str(draft.get('type') or '').strip(),
                'priority': priority,
                'tags': tags,
            }, ensure_ascii=False)
        return self.suggestion_result(
            suggestion_content=content,
            context=context,
            targets=[self._target_from_trigger_context(context)],
            suggestion_reason=reason,
        )
