"""Run one agent case through the real phase workflow without SeaDB.

The records, search hits and model are supplied by the case and the caller;
everything else is the production code path (``AgentService.prepare`` and
``AgentPhasePromoter``).
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import yaml
from jsonschema import Draft202012Validator

from seaqa_ai.agent.agent_service import AgentService
from seaqa_ai.agent.agent_service.phase_promoters import AgentPhasePromoter
from seaqa_ai.agent.agent_service.run_rebuilder import _resume_from, restore_phase_outputs
from seaqa_ai.agent.models import AgentEvent, AgentTask
from seaqa_ai.agent.utils.loaded_records import LoadedRecords
from seaqa_ai.agent.utils.record_identity import identity_key
from seaqa_ai.config import DEFAULT_LLM_MODEL, LLM_MODEL_ID_MODELS_MAP
from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils import get_tools_map
from seaqa_ai.agent.utils.react import AgentFinalOutputSchemaError, AgentRetryExceededError
from seaqa_ai.utils.constants import ExtraSourceType
from seaqa_ai.utils.llm_api import get_responses_client_by_model_id

from tests.agent.support.matchers import validate_matchers
from tests.agent.support.fakes import FakeAIServices, NullDataLogger


SCHEMA_PATH = Path(__file__).with_name('case_schema.json')
PROJECT_UUID = 'a' * 32


@dataclass
class AgentRunResult:
    phases: dict
    suggestions: list
    tracer_rows: list
    search_calls: list
    model_id: str | None
    token_usage: dict = field(default_factory=dict)
    error: str = ''

    @property
    def tool_names(self):
        return [item['tool_name'] for item in self.suggestions]


# ---- case loading -----------------------------------------------------------

def _validator(definition):
    schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
    return Draft202012Validator({'$ref': f'#/$defs/{definition}', '$defs': schema['$defs']})


def _validate(document, definition, where):
    errors = sorted(
        _validator(definition).iter_errors(document), key=lambda error: list(error.path),
    )
    if errors:
        details = '; '.join(
            f'{"/".join(str(part) for part in error.absolute_path) or "<root>"}: {error.message}'
            for error in errors[:5]
        )
        raise ValueError(f'{where} is invalid: {details}')


def validate_case(case, where='case'):
    """Check the shape of an input case and that its references can be loaded."""
    document = {key: value for key, value in case.items() if key not in ('name', 'expected')}
    _validate(document, 'input', where)

    present = {
        identity_key(item['identity'])
        for item in case['records']
    }
    trigger = case['task']
    trigger_key = (trigger['source_type'], trigger['connection_id'], trigger['record_id'])
    if trigger_key not in present:
        raise ValueError(f'{where}: the trigger record {trigger_key} is not in records')

    for tool, hits in (case.get('search_results') or {}).items():
        for index, hit in enumerate(hits):
            if '_id' not in hit:
                continue
            connection_id = None if ExtraSourceType.is_valid(hit['type']) else hit.get('connection_id')
            key = (hit['type'], connection_id, hit['_id'])
            if key not in present:
                raise ValueError(
                    f'{where}: search_results.{tool}[{index}] refers to {key}, '
                    'which is not in records; Handling would fail to load it'
                )


def _suggestion_action_names():
    return {
        name for name, tool in get_tools_map().items()
        if issubclass(tool, BasicAgentSuggestionTool)
    } | {'no_op'}


def _check_action_names(expected, where):
    """Reject action names that are not suggestion tools, so a typo cannot drop a rule."""
    allowed = _suggestion_action_names()
    suggestions = expected.get('suggestions') or {}
    for field in ('forbidden', 'exactly_one_of'):
        for index, name in enumerate(suggestions.get(field) or []):
            if name not in allowed:
                raise ValueError(f'{where}: suggestions.{field}[{index}]: unknown action {name!r}')
    for index, call in enumerate(suggestions.get('calls') or []):
        name = call.get('tool')
        if name not in allowed:
            raise ValueError(f'{where}: suggestions.calls[{index}].tool: unknown action {name!r}')


def validate_expectations(expected, where='expected'):
    _validate(expected, 'expected', where)
    validate_matchers(expected)
    _check_action_names(expected, where)


def load_case(path):
    case_dir = Path(path)
    with (case_dir / 'input.yaml').open(encoding='utf-8') as handle:
        case = yaml.safe_load(handle)
    validate_case(case, f'{case_dir.name}/input.yaml')
    expected_path = case_dir / 'expected.yaml'
    if expected_path.is_file():
        with expected_path.open(encoding='utf-8') as handle:
            expected = yaml.safe_load(handle)
        validate_expectations(expected, f'{case_dir.name}/expected.yaml')
        case['expected'] = expected
    case['name'] = case_dir.name
    return case


def case_variables(case):
    """Values that ``$name`` references in expected.yaml can use."""
    trigger = case['task']
    trigger_record = {}
    for item in case['records']:
        if identity_key(item['identity']) == (
            trigger['source_type'], trigger['connection_id'], trigger['record_id'],
        ):
            trigger_record = item['record']
    return {
        'available_labels': trigger_record.get('available_labels', []),
        'trigger_record': trigger_record,
    }


# ---- model selection ----------------------------------------------------------

def resolve_model_id(case, model_id=None):
    """Pick the model id and reject an explicit id that is not configured."""
    explicit = str(model_id or os.environ.get('SEAQA_TEST_MODEL') or '').strip()
    if not explicit:
        settings = (case.get('project') or {}).get('settings') or {}
        case_model = (settings.get('agent') or {}).get('model')
        if isinstance(case_model, str) and case_model.strip():
            explicit = case_model.strip()
    if explicit:
        if explicit not in LLM_MODEL_ID_MODELS_MAP:
            available = ', '.join(sorted(LLM_MODEL_ID_MODELS_MAP))
            raise ValueError(
                f'Unknown model id {explicit!r}. Configured model ids: {available}'
            )
        return explicit
    default_model = (DEFAULT_LLM_MODEL or {}).get('model')
    if not default_model:
        raise ValueError('No default LLM model is configured')
    return default_model


class CaseRunError(Exception):
    """The model failed after its retries. ``result`` is everything completed before that."""

    def __init__(self, result):
        self.result = result
        super().__init__(result.error)


def _assemble(promoter, prepared, ai_services, model_id, error=''):
    suggestions = []
    for row in prepared.tracer:
        if row.get('action_type') != 'suggestion':
            continue
        copied = dict(row)
        payload = copied.get('suggestion_payload')
        if isinstance(payload, str):
            copied['suggestion_payload'] = json.loads(payload or '{}')
        suggestions.append(copied)
    rows = list(prepared.tracer)
    return AgentRunResult(
        phases=dict(promoter.result),
        suggestions=suggestions,
        tracer_rows=rows,
        search_calls=list(ai_services.calls),
        model_id=model_id,
        token_usage=_sum_tokens(rows),
        error=error,
    )


# ---- running ------------------------------------------------------------------

def _parse_time(value, fallback):
    if not value:
        return fallback
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace('Z', '+00:00'),
    )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_task(case):
    now = datetime.now(timezone.utc)
    task_input = case['task']
    event = AgentEvent(
        project_uuid=PROJECT_UUID,
        source_type=task_input['source_type'],
        record_id=int(task_input['record_id']),
        event_type=task_input['event']['type'],
        event=task_input['event'],
        connection_id=task_input.get('connection_id'),
        modified_time=_parse_time(task_input.get('modified_time'), now),
        received_at=now,
    )
    return AgentTask(
        schema_version=1,
        task_id='agent-test',
        project_uuid=PROJECT_UUID,
        trigger_event=event,
        created_at=now,
    )


def _sum_tokens(rows):
    totals = {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}
    for row in rows:
        statistics = row.get('statistics')
        if not isinstance(statistics, dict):
            continue
        for key in totals:
            totals[key] += int(statistics.get(key) or 0)
    return totals


def prepare_case(case):
    """Build the task, fake app and PreparedRun of a case, as production does.

    Returns ``(task, app, prepared)``. Raises MissingRequiredLinkedTicket for a
    trigger that needs a linked Ticket and has none.
    """
    validate_case(case)
    task = build_task(case)
    app = SimpleNamespace(
        ai_services=FakeAIServices(case.get('search_results') or {}),
        seadb_service=None,
        data_logger=NullDataLogger(),
        username_resolver=None,
        seaqa_web_api=None,
    )
    loaded_records = LoadedRecords()
    for item in case['records']:
        identity = item['identity']
        loaded_records.add(
            identity['source_type'], identity['connection_id'], identity['record_id'],
            item['record'],
        )
    prepared = AgentService(app, case['project']).prepare(task, loaded_records)
    return task, app, prepared


def run_case(case, *, responses_client=None, model_id=None, mode='start',
             user_instruction='', previous_drafts=None, prior_phases=None):
    """Run a case and return an AgentRunResult.

    ``mode='start'`` runs Prelude, Analysis and Handling like a new event.
    ``mode='regeneration'`` rebuilds from ``prior_phases`` (a dict with the
    stored ``prelude`` and ``analysis`` outputs) exactly like a user request
    to regenerate suggestions.
    """
    if mode not in ('start', 'regeneration'):
        raise ValueError(f'unknown mode {mode!r}')
    task, app, prepared = prepare_case(case)
    ai_services = app.ai_services

    resolved_model_id = None
    if responses_client is None:
        resolved_model_id = resolve_model_id(case, model_id)
        print(f'[agent-test] model={resolved_model_id}', flush=True)
        responses_client = get_responses_client_by_model_id(app.data_logger, resolved_model_id)

    promoter = AgentPhasePromoter(
        app,
        task,
        responses_client,
        prepared.tracer,
        prepared.context,
        prepared.loaded_records,
        project_prompt=prepared.project_prompt or '',
        ticket_rules=prepared.ticket_rules,
    )

    try:
        if mode == 'start':
            promoter.start()
        else:
            phase_actions = [
                {'action_type': phase, 'status': 'completed', 'result': json.dumps(output)}
                for phase, output in (prior_phases or {}).items()
            ]
            outputs = restore_phase_outputs(promoter, phase_actions)
            promoter.result.update(outputs)
            promoter.start_regeneration(
                user_instruction,
                previous_drafts,
                resume_from=_resume_from(outputs),
            )
    except (AgentRetryExceededError, AgentFinalOutputSchemaError) as error:
        cause = error.__cause__ or error
        message = f'{type(cause).__name__}: {error}'
        raise CaseRunError(_assemble(
            promoter, prepared, ai_services, resolved_model_id, message,
        )) from error
    return _assemble(promoter, prepared, ai_services, resolved_model_id)
