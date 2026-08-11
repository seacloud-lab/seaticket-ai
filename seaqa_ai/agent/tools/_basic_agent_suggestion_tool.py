"""Shared pending-suggestion action behavior for agent tools."""

from seaqa_ai.utils.tools import BasicTool


class BasicAgentSuggestionTool(BasicTool):
    """Build trace descriptors for suggestions without exposing action identity to the model."""

    action_type = 'suggestion'
    action_status = 'pending'
    suggestion_text = ''
    suggestion_content = None

    @staticmethod
    def _target_from_context(context):
        context = context or {}
        return {
            'source_type': context.get('source_type', ''),
            'source_id': context.get('source_id', ''),
            'source_title': context.get('source_title', ''),
        }

    @staticmethod
    def _target_from_trigger_context(context):
        context = context or {}
        return {
            'source_type': context.get('trigger_source_type', context.get('source_type', '')),
            'source_id': context.get('trigger_source_id', context.get('source_id', '')),
            'source_title': context.get('trigger_source_title', context.get('source_title', '')),
        }

    @classmethod
    def _normalize_target(cls, target):
        if not isinstance(target, dict):
            raise ValueError('target must be an object')
        source_type = str(target.get('source_type', '') or '').strip()
        source_id = target.get('source_id')
        record_id = target.get('record_id')
        connection_id = target.get('connection_id')
        if not source_type:
            raise ValueError('target requires source_type')
        if source_id not in (None, ''):
            source_id = str(source_id).strip()
        elif record_id not in (None, ''):
            if connection_id is not None:
                if not isinstance(connection_id, int) or isinstance(connection_id, bool):
                    raise ValueError('target connection_id must be an integer or null')
                if not isinstance(record_id, int) or isinstance(record_id, bool):
                    raise ValueError('target record_id must be an integer')
                source_id = f'{connection_id}_{record_id}'
            else:
                source_id = str(record_id)
        else:
            raise ValueError('target requires source_id or record_id')
        if not source_id:
            raise ValueError('target source_id must not be empty')
        return {
            'source_type': source_type,
            'source_id': source_id,
            'source_title': str(target.get('source_title', '') or '').strip(),
        }

    def suggestion_result(
        self, *, suggestion_content='', context=None, targets=None, suggestion_reason='', suggestion_text=None,
        tool_name=None,
    ):
        if targets is None:
            targets = [self._target_from_context(context)]
        normalized_targets = [self._normalize_target(target) for target in targets]
        return {
            'status': self.action_status,
            'suggestion_text': self.suggestion_text if suggestion_text is None else suggestion_text,
            'suggestion_content': self.suggestion_content if suggestion_content is None else suggestion_content,
            'suggestion_reason': str(suggestion_reason or '').strip(),
            'trace_targets': normalized_targets,
            'trace_tool_name': tool_name or self.tool.get('name'),
        }
