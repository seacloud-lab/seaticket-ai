import json
import logging
from copy import deepcopy
from seaqa_ai.config import THOUGHT_PROCESS_ENABLED, THOUGHT_PROCESS_RECORD_TOOL_DETAILS, THOUGHT_PROCESS_RECORD_SYSTEM_PROMPTS, THOUGHT_PROCESS_RECORD_USER_INPUT_RAW
from seaqa_ai.utils import remove_sources_content_and_snippets, now_iso, object_to_json_str, parse_json
from seaqa_ai.utils.tools import SEARCH_TOOL_NAMES
from seaqa_ai.seadb_models import SchemaTables
from seaqa_ai.agent.utils.constants import (
    MAX_CONTENT_CHARS,
    AGENT_AUTO_ACTION_QUEUE_NAME,
)

logger = logging.getLogger(__name__)

def _truncate_string(value, max_chars):
    if not isinstance(value, str):
        return value
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f'\n... [truncated, original_length={len(value)}]'

class AgentActionRecorder:
    def __init__(self, project_uuid, seadb_api, mq=None, auto_confirm_tools=None):
        self.project_uuid = project_uuid
        self.seadb_api = seadb_api
        self.mq = mq
        self.auto_confirm_tools = set(auto_confirm_tools or set())

    def __bool__(self):
        return True

    def __call__(
        self,
        run_id,
        source_type,
        source_id,
        source_title,
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
        suggestion_text='',
        suggestion_content='',
        sources=None,
        statistics=''
    ):
        """Insert a new agent_actions row. Returns _pk."""
        if isinstance(sources, list):
            sources_payload = json.dumps(sources, ensure_ascii=False)
        elif isinstance(sources, str):
            sources_payload = sources
        else:
            sources_payload = '[]'

        if not isinstance(result, str):
            result = object_to_json_str(result)
        else:
            result = _truncate_string(result, MAX_CONTENT_CHARS)

        # record phase' system prompt
        prompt = prompt or ''
        input = input or ''
        if not isinstance(input, str):
            input = object_to_json_str(input)
        else:
            input = _truncate_string(input, MAX_CONTENT_CHARS)

        if not THOUGHT_PROCESS_ENABLED or \
            not THOUGHT_PROCESS_RECORD_SYSTEM_PROMPTS:
            prompt = ''
        if not THOUGHT_PROCESS_ENABLED or not THOUGHT_PROCESS_RECORD_USER_INPUT_RAW:
            input = ''

        if action_type not in (
            'event',
            'analysis',
            'summary',
            'error',
        ):
            # disable recording phase-level prompt/input in common tool_call steps
            prompt = ''
            input = ''
        elif action_type == 'error' and not record_phase_context:
            prompt = ''
            input = ''

        # record tool call detail
        if not THOUGHT_PROCESS_ENABLED or not THOUGHT_PROCESS_RECORD_TOOL_DETAILS:
            tool_arguments = '{}'
        else:
            tool_arguments = json.dumps(tool_arguments)

        new_observation = deepcopy(observation) or ''
        if not THOUGHT_PROCESS_ENABLED:
            new_observation = ''
        elif tool_name in SEARCH_TOOL_NAMES:
            parsed = parse_json(new_observation)
            if isinstance(parsed, list):
                if not THOUGHT_PROCESS_RECORD_TOOL_DETAILS:
                    parsed = remove_sources_content_and_snippets(parsed)
                new_observation = json.dumps(parsed, ensure_ascii=False)
        elif not isinstance(new_observation, str):
            new_observation = json.dumps(new_observation)
        else:
            new_observation = _truncate_string(new_observation, MAX_CONTENT_CHARS)
    
        row = {
            'run_id': run_id,
            'source_type': source_type or '',
            'source_id': str(source_id) if source_id is not None else '',
            'source_title': source_title or '',
            'action_type': action_type,
            'phase': phase,
            'result': str(result) if result is not None else '',
            'prompt': prompt if THOUGHT_PROCESS_RECORD_SYSTEM_PROMPTS and prompt is not None else '',
            'input': input if THOUGHT_PROCESS_RECORD_USER_INPUT_RAW and input is not None else '',
            'step': step,
            'tool_name': tool_name or '',
            'tool_arguments': tool_arguments or '',
            'observation': new_observation or '',
            'status': action_status,
            'suggestion_text': suggestion_text or '',
            'suggestion_content': suggestion_content or '',
            'sources': sources_payload,
            'statistics': statistics or '',
            'created_at': now_iso(),
        }
        try:
            res = self.seadb_api.insert_rows(self.project_uuid, SchemaTables.AGENT_ACTIONS.table_name(), [row])
            pks = res.get('pks', [])
            assert len(pks) == 1, f'Expected 1 PK, got {len(pks)}'
            action_id = pks[0]
            self._enqueue_auto_action_if_needed(
                action_id=action_id,
                tool_name=tool_name,
                action_status=action_status,
            )
            return action_id
        except Exception as e:
            logger.warning(f'Failure to record agent action: {e}')

    def _enqueue_auto_action_if_needed(
        self,
        action_id,
        tool_name,
        action_status,
    ):
        if action_status != 'pending':
            return
        if not tool_name or tool_name not in self.auto_confirm_tools:
            return
        if self.mq is None:
            return
        message = {
            'project_uuid': self.project_uuid,
            'action_id': action_id,
        }
        try:
            self.mq.lpush(AGENT_AUTO_ACTION_QUEUE_NAME, json.dumps(message))
        except Exception as e:
            logger.warning('Failed to enqueue auto action %s: %s', action_id, e)
