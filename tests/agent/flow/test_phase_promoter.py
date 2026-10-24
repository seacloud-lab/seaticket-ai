"""Run the whole Prelude, Analysis, Handling workflow of every event with a scripted model."""
import pytest

from seaqa_ai.agent.events_manager.constants import SUPPORTED_EVENT_TYPES

from tests.agent.support.builders import (
    analysis_output, prelude_output, text, tool_call, tool_calls,
)
from tests.agent.support.calls import user_payload
from tests.agent.support.fakes import ScriptedResponsesClient
from tests.agent.support.harness import run_case
from tests.agent.support.scenarios import expected_trace, handling_tools, scenario

EVENTS = sorted(SUPPORTED_EVENT_TYPES)


def _prelude_for(s):
    selected = [s.trigger_identity]
    if s.owner_ticket_identity and s.owner_ticket_identity != s.trigger_identity:
        selected.append(s.owner_ticket_identity)
    return prelude_output(*[
        (item['source_type'], item['connection_id'], item['record_id']) for item in selected
    ])


@pytest.mark.parametrize('event', EVENTS)
def test_every_handling_tool_creates_a_suggestion_for_the_right_target(event):
    s = scenario(event)
    tools = handling_tools(event)
    client = ScriptedResponsesClient([
        _prelude_for(s),
        analysis_output(event, tools),
        tool_calls(*[tool_call(name, **s.tool_arguments[name]) for name in tools]),
        text('Created the selected pending suggestions.'),
    ])

    run = run_case(s.case, responses_client=client)

    assert not [row for row in run.tracer_rows if row['action_type'] == 'error']
    traced = [expected_trace(s, name) for name in tools]
    assert [
        (row['tool_name'], row['target_item_type'], row['target_item_id'])
        for row in run.suggestions
    ] == traced
    assert all(row['status'] == 'pending' for row in run.suggestions)
    assert client.scripts == []


@pytest.mark.parametrize('event', EVENTS)
def test_handling_receives_exactly_the_actions_analysis_selected(event):
    s = scenario(event)
    selected = handling_tools(event)[:1]
    client = ScriptedResponsesClient([
        _prelude_for(s),
        analysis_output(event, selected),
        text('Nothing to do.'),
    ])

    run_case(s.case, responses_client=client)

    assert user_payload(client.calls[-1])['recommended_actions'] == selected


@pytest.mark.parametrize('event', EVENTS)
def test_no_op_skips_handling(event):
    s = scenario(event)
    client = ScriptedResponsesClient([
        _prelude_for(s),
        analysis_output(event, ['no_op']),
    ])

    run = run_case(s.case, responses_client=client)

    assert run.phases['analysis']['skip_handling'] is True
    assert 'handling' not in run.phases
    assert run.suggestions == []
    assert len(client.calls) == 2
