"""Rules every suggestion tool must keep. None of these call a model."""
import inspect
import json

import pytest
from jsonschema import Draft202012Validator

from seaqa_ai.agent.models.phase import AgentPhaseSpec
from seaqa_ai.agent.tools._basic_agent_suggestion_tool import BasicAgentSuggestionTool
from seaqa_ai.agent.utils import get_skill_map, get_tools_map
from seaqa_ai.utils.tools import InvalidToolArgumentsException, OpenAIToolExecutor

from tests.agent.support.harness import prepare_case
from tests.agent.support.scenarios import scenario

SUGGESTION_TOOLS = {
    name: tool for name, tool in sorted(get_tools_map().items())
    if issubclass(tool, BasicAgentSuggestionTool)
}


def _event_using(tool_name):
    for event in sorted(get_skill_map()):
        if tool_name in AgentPhaseSpec.get_skill_metadata(event, 'handling')['tools']:
            return event
    raise AssertionError(f'no event uses {tool_name}')


def _executor_with(instance):
    executor = OpenAIToolExecutor()
    executor.register(instance.execute, instance.tool)
    return executor


def _call(instance, arguments, prepared, app):
    parameters = inspect.signature(instance.execute).parameters
    kwargs = dict(arguments)
    if 'context' in parameters:
        kwargs['context'] = prepared.context
    if 'app' in parameters:
        kwargs['app'] = app
    return instance.execute(**kwargs)


def test_there_are_suggestion_tools_to_check():
    assert len(SUGGESTION_TOOLS) >= 9


@pytest.mark.parametrize('name', SUGGESTION_TOOLS)
def test_the_tool_schema_is_valid_and_named_after_its_module(name):
    definition = SUGGESTION_TOOLS[name].tool
    parameters = definition['parameters']

    assert definition['name'] == name
    assert definition['type'] == 'function'
    Draft202012Validator.check_schema(parameters)
    assert parameters['additionalProperties'] is False
    assert set(parameters['required']) <= set(parameters['properties'])
    if definition['strict']:
        # Strict mode asks the provider to guarantee the arguments, which needs every field required.
        assert set(parameters['required']) == set(parameters['properties'])


@pytest.mark.parametrize('name', SUGGESTION_TOOLS)
def test_a_valid_call_creates_a_pending_suggestion(name):
    s = scenario(_event_using(name))
    _task, app, prepared = prepare_case(s.case)
    instance = SUGGESTION_TOOLS[name]()
    arguments = s.tool_arguments[name]

    _executor_with(instance).validate_arguments(name, dict(arguments))
    result = _call(instance, arguments, prepared, app)

    assert result['status'] == 'pending'
    assert result['suggestion_reason'].strip()
    assert result['trace_targets']
    for target in result['trace_targets']:
        assert target['source_type'] and target['source_id']
    json.dumps(result['suggestion_payload'])


@pytest.mark.parametrize('name', SUGGESTION_TOOLS)
def test_an_empty_reason_is_rejected(name):
    s = scenario(_event_using(name))
    _task, app, prepared = prepare_case(s.case)
    instance = SUGGESTION_TOOLS[name]()
    arguments = {**s.tool_arguments[name], 'reason': '   '}

    with pytest.raises(ValueError):
        _call(instance, arguments, prepared, app)


@pytest.mark.parametrize('name', SUGGESTION_TOOLS)
def test_arguments_outside_the_schema_are_rejected_before_the_tool_runs(name):
    s = scenario(_event_using(name))
    instance = SUGGESTION_TOOLS[name]()
    arguments = {**s.tool_arguments[name], 'made_up_argument': 1}

    with pytest.raises(InvalidToolArgumentsException, match='made_up_argument'):
        _executor_with(instance).validate_arguments(name, arguments)
