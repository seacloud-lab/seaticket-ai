"""Shared pending-suggestion action behavior for agent tools."""

from seaqa_ai.utils.tools import BasicTool


class BasicAgentSuggestionTool(BasicTool):
    """Build trace descriptors for suggestions without exposing action identity to the model."""

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = ''

    @staticmethod
    def _target_from_context(context):
        context = context or {}
        return {
            'source_type': context.get('source_type', ''),
            'source_id': context.get('source_id', ''),
            'source_title': context.get('source_title', ''),
        }

    @classmethod
    def _normalize_target(cls, target):
        if not isinstance(target, dict):
            raise ValueError('target must be an object')
        source_type = str(target.get('source_type', '') or '').strip()
        record_id = target.get('record_id', target.get('source_id'))
        if not source_type or record_id in (None, ''):
            raise ValueError('target requires source_type and record_id')
        return {
            'source_type': source_type,
            'source_id': str(record_id),
            'source_title': str(target.get('source_title', '') or '').strip(),
        }

    def suggestion_result(
        self, *, content='', context=None, targets=None, suggestion_text=None, tool_name=None,
    ):
        if targets is None:
            targets = [self._target_from_context(context)]
        normalized_targets = [self._normalize_target(target) for target in targets]
        return {
            'status': self.action_status,
            'suggestion_content': str(content or ''),
            'trace_targets': normalized_targets,
            'suggestion_text': self.suggestion_text if suggestion_text is None else suggestion_text,
            'trace_tool_name': tool_name or self.tool.get('name'),
        }
