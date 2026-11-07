"""Events that belong to a linked Ticket, and ticket rules."""
import pytest

from seaqa_ai.agent.agent_service import MissingRequiredLinkedTicket

from tests.agent.support.builders import (
    analysis_output, github_issue_record, identity, prelude_output, text,
)
from tests.agent.support.calls import (
    get_record_calls, ordered_records_prompts, system_messages,
)
from tests.agent.support.fakes import ScriptedResponsesClient
from tests.agent.support.harness import run_case
from tests.agent.support.scenarios import (
    EXTERNAL_CONNECTION_ID, EXTERNAL_RECORD_ID, LINKED_TICKET_ID, TRIGGER_ID, scenario,
)

RULE = 'Never close a ticket on a Friday.'


def _trigger(event):
    task = scenario(event).case['task']
    return (task['source_type'], task['connection_id'], task['record_id'])


def test_linked_event_injects_trigger_ticket_and_siblings_in_three_groups():
    event = 'github_issue_comment_added'
    s = scenario(event)
    client = ScriptedResponsesClient([
        prelude_output(_trigger(event), ('ticket', None, LINKED_TICKET_ID)),
        analysis_output(event, ['no_op']),
    ])

    run_case(s.case, responses_client=client)

    prelude_call = client.calls[0]
    assert [prompt.split(':')[0] for prompt in ordered_records_prompts(prelude_call)] == [
        'PreStep 1', 'PreStep 2', 'PreStep 3',
    ]
    assert get_record_calls(prelude_call) == [
        {'source_type': 'github_issue', 'connection_id': 1, 'record_id': TRIGGER_ID},
        {'source_type': 'ticket', 'connection_id': None, 'record_id': LINKED_TICKET_ID},
        {'source_type': 'github_issue', 'connection_id': EXTERNAL_CONNECTION_ID,
         'record_id': EXTERNAL_RECORD_ID},
    ]


def test_records_not_linked_to_the_ticket_are_dropped_from_prelude_selection():
    event = 'github_issue_comment_added'
    s = scenario(event)
    stranger = github_issue_record(77, title='Not linked to the ticket')
    s.case['records'].append({
        'identity': identity('github_issue', 1, 77),
        'record': stranger,
    })
    client = ScriptedResponsesClient([
        prelude_output(
            _trigger(event), ('ticket', None, LINKED_TICKET_ID), ('github_issue', 1, 77),
            ('github_issue', EXTERNAL_CONNECTION_ID, EXTERNAL_RECORD_ID),
        ),
        analysis_output(event, ['no_op']),
    ])

    run = run_case(s.case, responses_client=client)

    selected = run.phases['prelude']['event_related_records']
    assert identity('github_issue', 1, 77) not in selected
    assert selected == [
        identity('github_issue', 1, TRIGGER_ID),
        identity('ticket', None, LINKED_TICKET_ID),
        identity('github_issue', EXTERNAL_CONNECTION_ID, EXTERNAL_RECORD_ID),
    ]


@pytest.mark.parametrize('event', [
    'general_task_updated', 'jira_issue_updated', 'linear_issue_updated',
])
def test_event_that_requires_a_linked_ticket_is_skipped_without_one(event):
    s = scenario(event)
    for item in s.case['records']:
        item['record']['linked_ticket'] = None
    s.case['records'] = [
        item for item in s.case['records'] if item['identity']['source_type'] != 'ticket'
    ]
    client = ScriptedResponsesClient([])

    with pytest.raises(MissingRequiredLinkedTicket):
        run_case(s.case, responses_client=client)

    assert client.calls == []


def test_unlinked_comment_event_still_runs_with_the_trigger_alone():
    event = 'github_issue_comment_added'
    s = scenario(event)
    s.case['records'] = [
        item for item in s.case['records']
        if item['identity']['source_type'] == 'github_issue'
        and item['identity']['record_id'] == TRIGGER_ID
    ]
    s.case['records'][0]['record']['linked_ticket'] = None
    client = ScriptedResponsesClient([
        prelude_output(_trigger(event)),
        analysis_output(event, ['no_op']),
    ])

    run = run_case(s.case, responses_client=client)

    assert run.phases['analysis']['skip_handling'] is True
    assert ordered_records_prompts(client.calls[0]) == []


def _rule_settings():
    return {'lang': 'en', 'agent': {'model': None, 'ticket_rules': RULE}}


@pytest.mark.parametrize('event', ['ticket_over_due', 'github_issue_comment_added'])
def test_ticket_rules_reach_analysis_and_handling_when_a_ticket_owns_the_run(event):
    s = scenario(event, settings=_rule_settings())
    client = ScriptedResponsesClient([
        prelude_output(_trigger(event)),
        analysis_output(event, ['no_op'], skip_handling=False),
        text('Nothing to do.'),
    ])

    run_case(s.case, responses_client=client)

    prelude_call, analysis_call, handling_call = client.calls
    assert not any(RULE in message for message in system_messages(prelude_call))
    assert any(RULE in message for message in system_messages(analysis_call))
    assert any(RULE in message for message in system_messages(handling_call))


def test_ticket_rules_are_not_used_when_the_run_is_not_owned_by_a_ticket():
    event = 'github_issue_added'
    s = scenario(event, settings=_rule_settings())
    client = ScriptedResponsesClient([
        prelude_output(_trigger(event)),
        analysis_output(event, ['no_op']),
    ])

    run_case(s.case, responses_client=client)

    assert not any(
        RULE in message for call in client.calls for message in system_messages(call)
    )
