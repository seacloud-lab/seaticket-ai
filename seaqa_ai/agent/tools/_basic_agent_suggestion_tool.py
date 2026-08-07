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

    def get_suggestion_reason(self, *, tool_arguments, result, context):
        return str(tool_arguments.get('reason', '') or '').strip()

    def get_suggestion_text(self, *, tool_arguments, result, context):
        return self.suggestion_text

    def get_suggestion_content(self, *, tool_arguments, result, context):
        if isinstance(result, dict):
            return str(result.get('suggestion_content', '') or '')
        return ''

    def get_suggestion_targets(self, *, tool_arguments, result, context):
        return [self._target_from_context(context)]

    def get_react_traces(self, *, tool_arguments, result, context):
        reason = self.get_suggestion_reason(
            tool_arguments=tool_arguments, result=result, context=context,
        )
        content = self.get_suggestion_content(
            tool_arguments=tool_arguments, result=result, context=context,
        )
        suggestion_text = self.get_suggestion_text(
            tool_arguments=tool_arguments, result=result, context=context,
        )
        traces = []
        for target in self.get_suggestion_targets(
            tool_arguments=tool_arguments, result=result, context=context,
        ):
            if not isinstance(target, dict):
                continue
            source_type = str(target.get('source_type', '') or '').strip()
            source_id = str(target.get('source_id', '') or '').strip()
            if not source_type or not source_id:
                continue
            traces.append({
                'action_type': self.action_type,
                'action_status': self.action_status,
                'tool_name': target.get('tool_name') or self.tool.get('name'),
                'source_type': source_type,
                'source_id': source_id,
                'source_title': str(target.get('source_title', '') or '').strip(),
                'suggestion_reason': reason,
                'suggestion_text': target.get('suggestion_text') or suggestion_text,
                'suggestion_content': target.get('suggestion_content') or content,
            })
        return traces
