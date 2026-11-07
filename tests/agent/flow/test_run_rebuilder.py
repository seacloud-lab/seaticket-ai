"""regenerate_agent_suggestions: rebuilding a stored run against an in-memory SeaDB."""
import json
from types import SimpleNamespace

import pytest

from seaqa_ai.agent.agent_service import run_rebuilder
from seaqa_ai.seadb_models import SchemaTables

from tests.agent.support.builders import (
    analysis_output, prelude_output, text, tool_call, tool_calls, ticket_record,
)
from tests.agent.support.calls import system_messages
from tests.agent.support.fakes import FakeAIServices, FakeSeaDB, NullDataLogger, ScriptedResponsesClient

EVENT = 'github_issue_comment_added'
ISSUE_ID, TICKET_ID = 101, 7
RULE = 'Never reply to a reporter on a Friday.'


def _project(with_rules=True):
    agent = {'model': None}
    if with_rules:
        agent['ticket_rules'] = RULE
    return {
        'uuid': 'a' * 32,
        'settings': {'lang': 'en', 'agent': agent},
        'issue_connections': [{'id': 1, 'type': 'github_issue'}],
        'document_connections': [],
        'task_connections': [],
    }


def _seadb():
    ticket = ticket_record(TICKET_ID, title='Owner ticket', linked_connection_records=['1_101'])
    ticket['content'] = 'Ticket body.'
    return FakeSeaDB({
        SchemaTables.GITHUB_ISSUES.table_name(1): {ISSUE_ID: {
            '_pk': ISSUE_ID, 'issue_id': 9001, 'title': 'An issue', 'state': 'open',
            'labels': [], 'issue_type': '', 'linked_ticket': None, 'content': 'Issue body.',
        }},
        SchemaTables.TICKETS.table_name(): {TICKET_ID: ticket},
    })


def _run(owner_type='ticket'):
    return {
        '_pk': 5,
        'event': json.dumps({
            'type': EVENT, 'old_value': {}, 'new_value': {'content': 'A new comment.'},
            'trigger_source': {'type': 'github_issue', 'id': '1_101', 'title': 'An issue'},
        }),
        'started_at': '2026-10-22T08:00:00+00:00',
        'owner_source_type': owner_type,
        'owner_source_id': str(TICKET_ID) if owner_type == 'ticket' else '',
        'owner_source_title': 'Owner ticket',
    }


def _regenerate(monkeypatch, scripts, *, phase_actions=(), owner_type='ticket', with_rules=True):
    project = _project(with_rules)
    app = SimpleNamespace(
        ai_services=FakeAIServices(), seadb_service=_seadb(), data_logger=NullDataLogger(),
        username_resolver=None, seaqa_web_api=None,
        seaqa_db=SimpleNamespace(get_project_by_uuid=lambda uuid: project),
    )
    client = ScriptedResponsesClient(scripts)
    monkeypatch.setattr(run_rebuilder, '_build_agent_project_context', lambda app, project: project)
    monkeypatch.setattr(
        run_rebuilder, 'get_responses_client_by_model_id', lambda data_logger, model_id: client,
    )
    result = run_rebuilder.regenerate_agent_suggestions(
        app,
        project_uuid='a' * 32,
        run=_run(owner_type),
        phase_actions=list(phase_actions),
        user_instruction='Reply to the reporter.',
    )
    return result, client


def _selected():
    return prelude_output(('github_issue', 1, ISSUE_ID), ('ticket', None, TICKET_ID))


def _regeneration_scripts():
    return [
        tool_calls(tool_call('suggest_reply', reply='A reply.', reason='The user asked for it.')),
        text('Created the requested suggestion.'),
    ]


def _has_rule(call):
    return any(RULE in message for message in system_messages(call))


def test_rules_reach_a_rebuilt_analysis_when_the_run_is_owned_by_a_ticket(monkeypatch):
    """The trigger issue is not linked, so the owner only becomes a Ticket while rebuilding."""
    result, client = _regenerate(monkeypatch, [
        _selected(),
        analysis_output(EVENT, ['no_op']),
        *_regeneration_scripts(),
    ])

    prelude_call, analysis_call, _regeneration_call, _ = client.calls
    assert not _has_rule(prelude_call)
    assert _has_rule(analysis_call)
    assert [item['tool_name'] for item in result['suggestions']] == ['suggest_reply']


def test_rules_reach_analysis_when_the_run_resumes_from_it(monkeypatch):
    stored_prelude = {'action_type': 'prelude', 'status': 'completed',
                      'result': json.loads(_selected()['content'])}
    stored_prelude['result'] = json.dumps(stored_prelude['result'])

    _result, client = _regenerate(
        monkeypatch,
        [analysis_output(EVENT, ['no_op']), *_regeneration_scripts()],
        phase_actions=[stored_prelude],
    )

    assert _has_rule(client.calls[0])


def test_no_rules_are_used_when_no_ticket_owns_the_run(monkeypatch):
    _result, client = _regenerate(
        monkeypatch,
        [
            prelude_output(('github_issue', 1, ISSUE_ID)),
            analysis_output(EVENT, ['no_op']),
            *_regeneration_scripts(),
        ],
        owner_type='',
    )

    assert not any(_has_rule(call) for call in client.calls)


def test_nothing_is_added_when_the_project_has_no_rules(monkeypatch):
    _result, client = _regenerate(
        monkeypatch,
        [_selected(), analysis_output(EVENT, ['no_op']), *_regeneration_scripts()],
        with_rules=False,
    )

    assert not any(_has_rule(call) for call in client.calls)
    assert not any(
        'custom_ticket_rules' in message
        for call in client.calls for message in system_messages(call)
    )
