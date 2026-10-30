"""Regenerating suggestions from a stored run, as the user can ask for in the UI."""
import json

import pytest

from seaqa_ai.agent.models.phase import AgentPhaseSpec

from tests.agent.support.builders import (
    analysis_output, prelude_output, text, tool_call, tool_calls,
)
from tests.agent.support.calls import user_payload
from tests.agent.support.fakes import ScriptedResponsesClient
from tests.agent.support.harness import run_case
from tests.agent.support.scenarios import scenario

EVENT = 'github_issue_added'


def _stored(script):
    """The stored phase output behind a scripted model response."""
    return json.loads(script['content'])


def _prelude():
    return prelude_output(('github_issue', 1, 101))


def _analysis(*actions):
    return analysis_output(EVENT, list(actions), report='Stored analysis report.')


def _regeneration_tools():
    return list(AgentPhaseSpec.get_skill_metadata(EVENT, 'regeneration')['tools'])


def _reply_script():
    return [
        tool_calls(tool_call(
            'suggest_reply', reply='A rewritten reply.', reason='The user asked for it.',
        )),
        text('Created the requested suggestion.'),
    ]


def test_regeneration_from_stored_analysis_offers_every_regeneration_tool():
    s = scenario(EVENT)
    client = ScriptedResponsesClient(_reply_script())

    run = run_case(
        s.case,
        responses_client=client,
        mode='regeneration',
        user_instruction='Write a friendlier reply.',
        previous_drafts=[{
            'tool_name': 'suggest_reply',
            'suggestion_content': 'An old reply.',
            'suggestion_reason': 'old',
            'suggestion_payload': '{"k": 1}',
        }],
        prior_phases={
            'prelude': _stored(_prelude()),
            'analysis': _stored(_analysis('suggest_create_ticket')),
        },
    )

    payload = user_payload(client.calls[0])
    # The user may ask for any tool the skill allows, not just what Analysis chose.
    assert payload['recommended_actions'] == _regeneration_tools()
    assert payload['user_instruction'] == 'Write a friendlier reply.'
    assert payload['analysis_report'] == 'Stored analysis report.'
    assert payload['previous_drafts'][0]['suggestion_content'] == 'An old reply.'
    assert payload['previous_drafts'][0]['suggestion_payload'] == {'k': 1}
    assert 'regeneration' in run.phases
    assert [row['tool_name'] for row in run.suggestions] == ['suggest_reply']
    assert run.suggestions[0]['suggestion_content'] == 'A rewritten reply.'


@pytest.mark.parametrize('prior, expected_phases', [
    ({}, ['prelude', 'analysis', 'regeneration']),
    ({'prelude': True}, ['analysis', 'regeneration']),
    ({'prelude': True, 'analysis': True}, ['regeneration']),
])
def test_regeneration_resumes_from_the_first_missing_phase(prior, expected_phases):
    s = scenario(EVENT)
    scripts = []
    prior_phases = {}
    if 'prelude' not in prior:
        scripts.append(_prelude())
    else:
        prior_phases['prelude'] = _stored(_prelude())
    if 'analysis' not in prior:
        scripts.append(_analysis('suggest_create_ticket'))
    else:
        prior_phases['analysis'] = _stored(_analysis('suggest_create_ticket'))
    client = ScriptedResponsesClient(scripts + _reply_script())

    run = run_case(
        s.case,
        responses_client=client,
        mode='regeneration',
        user_instruction='Reply to the reporter.',
        prior_phases=prior_phases,
    )

    ran = [phase for phase in ('prelude', 'analysis', 'regeneration')
           if phase in run.phases and phase not in prior_phases]
    assert ran == expected_phases
    assert client.scripts == []
    assert len(client.calls) == len(expected_phases) + 1  # regeneration makes two requests


def test_regeneration_ignores_a_stored_skip_handling_decision():
    s = scenario(EVENT)
    client = ScriptedResponsesClient(_reply_script())

    run = run_case(
        s.case,
        responses_client=client,
        mode='regeneration',
        user_instruction='Reply anyway.',
        prior_phases={
            'prelude': _stored(_prelude()),
            'analysis': _stored(analysis_output(EVENT, ['no_op'])),
        },
    )

    assert user_payload(client.calls[0])['skip_handling'] is True
    assert [row['tool_name'] for row in run.suggestions] == ['suggest_reply']
