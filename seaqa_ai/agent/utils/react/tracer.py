import json
import logging
import uuid
from copy import deepcopy

from seaqa_ai.config import (
    THOUGHT_PROCESS_ENABLED,
    THOUGHT_PROCESS_RECORD_SYSTEM_PROMPTS,
    THOUGHT_PROCESS_RECORD_TOOL_DETAILS,
    THOUGHT_PROCESS_RECORD_USER_INPUT_RAW,
)
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.utils import (
    now_iso,
    parse_json,
    remove_sources_content_and_snippets,
)
from seaqa_ai.utils.tools import SEARCH_TOOL_NAMES


logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 5000


def _truncate_string(value, max_chars=MAX_CONTENT_CHARS):
    if not isinstance(value, str) or len(value) <= max_chars:
        return value
    return value[:max_chars] + f'\n... [truncated, original_length={len(value)}]'


class AgentReACTTracer(list):
    """Collect ReACT actions and persist them in one SeaDB batch."""

    def __init__(
        self,
        project_uuid,
        seadb_service,
        *,
        target_source_type=None,
        target_source_id=None,
        target_source_title=None,
    ):
        super().__init__()
        self.project_uuid = project_uuid
        self.seadb_service = seadb_service
        self.target_source_type = target_source_type
        self.target_source_id = target_source_id
        self.target_source_title = target_source_title

    def record(
        self,
        *,
        context=None,
        phase=None,
        record_phase_context=False,
        prompt=None,
        input=None,
        result=None,
        action_type,
        action_status,
        step,
        tool_name=None,
        tool_arguments=None,
        observation=None,
        suggestion_reason='',
        suggestion_text='',
        suggestion_content='',
        sources=None,
        statistics=None,
        source_type=None,
        source_id=None,
        source_title=None,
        trace_id=None,
    ):
        """Normalize an action and append its SeaDB row to this tracer."""
        context = context or {}
        if not isinstance(context, dict):
            raise TypeError('context must be a dictionary')
        row = self._build_row(
            run_id=context.get('run_id'),
            source_type=(
                source_type if source_type is not None
                else self.target_source_type if self.target_source_type is not None
                else context.get('source_type')
            ),
            source_id=(
                source_id if source_id is not None
                else self.target_source_id if self.target_source_id is not None
                else context.get('source_id')
            ),
            source_title=(
                source_title if source_title is not None
                else self.target_source_title if self.target_source_title is not None
                else context.get('source_title')
            ),
            trace_id=trace_id or context.get('trace_id') or uuid.uuid4().hex,
            phase=phase if phase is not None else context.get('phase'),
            record_phase_context=record_phase_context,
            prompt=prompt,
            input=input,
            result=result,
            action_type=action_type,
            action_status=action_status,
            step=step,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            observation=observation,
            suggestion_reason=suggestion_reason,
            suggestion_text=suggestion_text,
            suggestion_content=suggestion_content,
            sources=sources,
            statistics=statistics,
        )
        self.append(row)
        return row

    __call__ = record

    def flush(self):
        """Persist actions and return IDs of pending suggestion actions."""
        if not self:
            return []

        rows = [self._serialize_row(row) for row in self]
        try:
            response = self.seadb_service.insert_rows(
                self.project_uuid,
                SchemaTables.AGENT_ACTIONS.table_name(),
                rows,
            )
        except Exception:
            logger.warning('Failed to record agent ReACT actions.', exc_info=True)
            return []

        pks = response.get('pks', [])
        if len(pks) != len(rows):
            logger.warning(
                'Failed to map agent ReACT actions to inserted IDs: expected %s IDs, received %s.',
                len(rows), len(pks),
            )
            return []

        self.clear()
        return [
            action_id
            for row, action_id in zip(rows, pks)
            if row['action_type'] == 'suggestion' and row['status'] == 'pending'
        ]

    @staticmethod
    def _serialize_row(row):
        serialized_row = dict(row)
        for field in ('tool_arguments', 'statistics'):
            value = serialized_row.get(field)
            if value is None:
                serialized_row[field] = ''
            elif not isinstance(value, str):
                serialized_row[field] = json.dumps(value, ensure_ascii=False)
        return serialized_row

    @staticmethod
    def _build_row(
        *,
        run_id,
        source_type,
        source_id,
        source_title,
        trace_id,
        phase,
        record_phase_context,
        prompt,
        input,
        result,
        action_type,
        action_status,
        step,
        tool_name,
        tool_arguments,
        observation,
        suggestion_reason,
        suggestion_text,
        suggestion_content,
        sources,
        statistics,
    ):
        if isinstance(sources, list):
            sources_payload = json.dumps(sources, ensure_ascii=False)
        elif isinstance(sources, str):
            sources_payload = sources
        else:
            sources_payload = '[]'

        result = (
            _truncate_string(result)
            if isinstance(result, str)
            else json.dumps(result)
        )

        prompt = prompt or ''
        input = input or ''
        input = (
            _truncate_string(input)
            if isinstance(input, str)
            else json.dumps(result)
        )

        if not THOUGHT_PROCESS_ENABLED or not THOUGHT_PROCESS_RECORD_SYSTEM_PROMPTS:
            prompt = ''
        if not THOUGHT_PROCESS_ENABLED or not THOUGHT_PROCESS_RECORD_USER_INPUT_RAW:
            input = ''

        if action_type == 'tool_call':
            prompt = ''
            input = ''
        elif action_type == 'error' and not record_phase_context:
            prompt = ''
            input = ''

        if not THOUGHT_PROCESS_ENABLED or not THOUGHT_PROCESS_RECORD_TOOL_DETAILS:
            tool_arguments = {}
        else:
            tool_arguments = deepcopy(tool_arguments) if tool_arguments is not None else {}

        normalized_observation = deepcopy(observation) or ''
        if not THOUGHT_PROCESS_ENABLED:
            normalized_observation = ''
        elif tool_name in SEARCH_TOOL_NAMES:
            parsed_observation = parse_json(normalized_observation)
            if isinstance(parsed_observation, list):
                if not THOUGHT_PROCESS_RECORD_TOOL_DETAILS:
                    parsed_observation = remove_sources_content_and_snippets(parsed_observation)
                normalized_observation = json.dumps(parsed_observation, ensure_ascii=False)
        elif not isinstance(normalized_observation, str):
            normalized_observation = json.dumps(normalized_observation)
        else:
            normalized_observation = _truncate_string(normalized_observation)

        return {
            'run_id': run_id,
            'trace_id': trace_id,
            'source_type': source_type or '',
            'source_id': str(source_id) if source_id is not None else '',
            'source_title': source_title or '',
            'action_type': action_type,
            'phase': phase or '',
            'result': str(result) if result is not None else '',
            'prompt': prompt if THOUGHT_PROCESS_RECORD_SYSTEM_PROMPTS and prompt is not None else '',
            'input': input if THOUGHT_PROCESS_RECORD_USER_INPUT_RAW and input is not None else '',
            'step': step,
            'tool_name': tool_name or '',
            'tool_arguments': tool_arguments,
            'observation': normalized_observation or '',
            'status': action_status,
            'suggestion_reason': suggestion_reason or '',
            'suggestion_text': suggestion_text or '',
            'suggestion_content': suggestion_content or '',
            'sources': sources_payload,
            'statistics': deepcopy(statistics) if statistics is not None else None,
            'created_at': now_iso(),
        }
