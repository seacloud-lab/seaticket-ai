"""Static rules every event skill must keep. None of these call a model."""
import inspect
import json

import pytest

from seaqa_ai.agent.agent_service import AgentService, PreparedRun
from seaqa_ai.agent.agent_service.constants import (
    LINKED_TICKET_TRACE_EVENT_TYPES, REQUIRED_LINKED_TICKET_TRACE_EVENT_TYPES,
)
from seaqa_ai.agent.agent_service.phase_promoters import ALL_PHASES
from seaqa_ai.agent.events_manager.constants import (
    SUPPORTED_EVENT_TYPES, TASK_COMPLETION_EVENT_SPECS,
)
from seaqa_ai.agent.models.phase import AgentPhaseSpec
from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils import get_skill_map, get_tools_map
from seaqa_ai.agent.utils.final_output_schema_utils import FinalOutputSchemaUtils

from tests.agent.support.builders import analysis_output

EVENTS = sorted(get_skill_map())
PHASES = tuple(ALL_PHASES)
SEARCH_TOOLS = {'issues_search', 'documents_search', 'tasks_search'}


def _spec(event, phase):
    return ALL_PHASES[phase](
        event=event, phase=phase, message_input='{}', records_input=[], context={},
    )


def _metadata(event, phase):
    return AgentPhaseSpec.get_skill_metadata(event, phase)


def _recommendable_actions(event):
    declared = _metadata(event, 'analysis')['additional_output']['recommended_actions']
    return set(declared['items']['enum'])


def test_skills_exist_for_exactly_the_supported_events():
    assert set(get_skill_map()) == set(SUPPORTED_EVENT_TYPES)


def test_linked_ticket_event_sets_are_consistent():
    assert LINKED_TICKET_TRACE_EVENT_TYPES <= SUPPORTED_EVENT_TYPES
    assert REQUIRED_LINKED_TICKET_TRACE_EVENT_TYPES <= LINKED_TICKET_TRACE_EVENT_TYPES
    assert set(TASK_COMPLETION_EVENT_SPECS) <= REQUIRED_LINKED_TICKET_TRACE_EVENT_TYPES


@pytest.mark.parametrize('event', EVENTS)
def test_every_phase_has_a_skill_and_prompt_and_its_tools_resolve(event):
    for phase in PHASES:
        spec = _spec(event, phase)
        assert spec.skill.strip()
        assert spec.get_system_prompt().strip()
        assert spec.tools is not None  # raises when a tool name is unknown


@pytest.mark.parametrize('event', EVENTS)
def test_prelude_and_analysis_output_schemas_are_strict_compatible(event):
    for phase in ('prelude', 'analysis'):
        schema = _spec(event, phase).final_output_schema
        assert schema['type'] == 'object'
        strict = FinalOutputSchemaUtils.to_strict_response_schema(schema)
        json.dumps(strict['schema'])


@pytest.mark.parametrize('event', EVENTS)
def test_analysis_may_recommend_exactly_the_handling_tools_and_no_op(event):
    handling = set(_metadata(event, 'handling')['tools'])

    assert _recommendable_actions(event) == handling | {'no_op'}


@pytest.mark.parametrize('event', EVENTS)
def test_regeneration_offers_the_same_tools_as_handling(event):
    assert _metadata(event, 'regeneration')['tools'] == _metadata(event, 'handling')['tools']


@pytest.mark.parametrize('event', EVENTS)
def test_handling_tools_are_suggestion_tools_and_analysis_tools_are_read_only(event):
    tools_map = get_tools_map()
    for phase in ('handling', 'regeneration'):
        for name in _metadata(event, phase)['tools']:
            assert issubclass(tools_map[name], BasicAgentSuggestionTool), (event, phase, name)
    for name in _metadata(event, 'analysis')['tools']:
        assert name in SEARCH_TOOLS | {'get_record'}, (event, name)
    assert _metadata(event, 'prelude')['tools'] == ['get_record']


@pytest.mark.parametrize('event', EVENTS)
def test_test_builders_produce_valid_analysis_output(event):
    """The default analysis output used by flow tests must satisfy the skill schema."""
    actions = sorted(_metadata(event, 'handling')['tools'])[:1] or ['no_op']
    script = analysis_output(event, actions)

    FinalOutputSchemaUtils.validate_and_normalize_output(
        json.loads(script['content']), _spec(event, 'analysis').final_output_schema, ValueError,
    )


def test_prepare_keeps_the_signature_that_run_and_the_test_harness_rely_on():
    parameters = list(inspect.signature(AgentService.prepare).parameters)

    assert parameters == ['self', 'task', 'loaded_records']
    assert set(PreparedRun.__dataclass_fields__) == {
        'context', 'loaded_records', 'trigger_record', 'tracer', 'trace_context',
        'project_prompt', 'ticket_rules', 'model_id',
    }
