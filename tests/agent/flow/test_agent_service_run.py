"""AgentService.run end to end against an in-memory SeaDB: records, run bookkeeping, failures."""
import json
from types import SimpleNamespace

import pytest

from seaqa_ai import config
from seaqa_ai.agent.agent_service import AgentService
from seaqa_ai.agent.utils.react import AgentRetryExceededError
from seaqa_ai.seadb_models import SchemaTables

from tests.agent.support.builders import (
    analysis_output, make_case, prelude_output, text, tool_call, tool_calls, ticket_record,
)
from tests.agent.support.fakes import FakeAIServices, FakeSeaDB, NullDataLogger, ScriptedResponsesClient
from tests.agent.support.harness import build_task

TICKET_ID = 7
RUNS = SchemaTables.AGENT_RUNS.table_name()
ACTIONS = SchemaTables.AGENT_ACTIONS.table_name()
TICKETS = SchemaTables.TICKETS.table_name()


def _ticket_row():
    row = ticket_record(TICKET_ID, title='Overdue ticket', assignees=['alice'],
                        due_date='2026-10-01')
    row['content'] = 'The ticket body.'
    return row


def _setup(monkeypatch, scripts, *, settings=None):
    case = make_case(
        'ticket_over_due', source_type='ticket', connection_id=None, record_id=TICKET_ID,
        record=_ticket_row(), settings=settings,
    )
    seadb = FakeSeaDB({TICKETS: {TICKET_ID: _ticket_row()}})
    app = SimpleNamespace(
        ai_services=FakeAIServices(), seadb_service=seadb, data_logger=NullDataLogger(),
        username_resolver=None, seaqa_web_api=None,
    )
    client = ScriptedResponsesClient(scripts)
    monkeypatch.setattr(
        'seaqa_ai.agent.agent_service.get_responses_client_by_model_id',
        lambda data_logger, model_id: client,
    )
    return AgentService(app, case['project']), build_task(case), seadb, client


def _notify_scripts():
    return [
        prelude_output(('ticket', None, TICKET_ID)),
        analysis_output('ticket_over_due', ['suggest_notify_assignee']),
        tool_calls(tool_call(
            'suggest_notify_assignee', message='The ticket is overdue.', reason='Past its due date.',
        )),
        text('Created the reminder suggestion.'),
    ]


def test_a_successful_run_creates_the_run_saves_actions_then_finishes_the_run(monkeypatch):
    service, task, seadb, client = _setup(monkeypatch, _notify_scripts())

    service.run(task)

    assert [(kind, table) for kind, table, _ in seadb.operations] == [
        ('insert', RUNS), ('insert', ACTIONS), ('update', RUNS), ('update', TICKETS),
    ]
    (run_row,) = seadb.writes('insert', RUNS)[0]
    assert run_row['status'] == 'running'
    assert (run_row['owner_source_type'], run_row['owner_source_id']) == ('ticket', str(TICKET_ID))
    event = json.loads(run_row['event'])
    assert event['trigger_source'] == {
        'type': 'ticket', 'id': str(TICKET_ID), 'title': 'Overdue ticket',
    }
    assert 'content' not in event['new_value']

    actions = seadb.writes('insert', ACTIONS)[0]
    assert {row['run_id'] for row in actions} == {1}
    suggestions = [row for row in actions if row['action_type'] == 'suggestion']
    assert [row['tool_name'] for row in suggestions] == ['suggest_notify_assignee']
    assert suggestions[0]['status'] == 'pending'
    assert not [row for row in actions if row['action_type'] == 'error']

    (finish,) = seadb.writes('update', RUNS)[0]
    assert finish['pk'] == 1
    assert finish['row']['status'] == 'completed'
    assert finish['row']['suggestions_status'] == 'pending'
    (processed,) = seadb.writes('update', TICKETS)[0]
    assert processed['pk'] == TICKET_ID
    assert processed['row']['last_agent_processed_at']
    assert client.scripts == []


def test_ticket_rules_from_the_project_settings_reach_the_model(monkeypatch):
    rule = 'Remind the assignee at most once a day.'
    settings = {'lang': 'en', 'agent': {'model': None, 'ticket_rules': rule}}
    service, task, _seadb, client = _setup(monkeypatch, _notify_scripts(), settings=settings)

    service.run(task)

    analysis_call = client.calls[1]
    assert any(
        rule in item['content'] for item in analysis_call['input']
        if isinstance(item, dict) and item.get('role') == 'system'
    )


def test_a_trigger_that_needs_a_linked_ticket_is_skipped_quietly(monkeypatch):
    case = make_case(
        'jira_issue_updated', source_type='jira_issue', connection_id=4, record_id=11,
        record={'_pk': 11, 'title': 'Jira issue', 'linked_ticket': None,
                'content': {'content': 'x', 'latest_comment': None, 'history_comments': []}},
    )
    seadb = FakeSeaDB({SchemaTables.JIRA_ISSUES.table_name(4): {11: {
        '_pk': 11, 'issue_id': 'J-1', 'title': 'Jira issue', 'linked_ticket': None,
        'content': 'x',
    }}})
    app = SimpleNamespace(
        ai_services=FakeAIServices(), seadb_service=seadb, data_logger=NullDataLogger(),
        username_resolver=None, seaqa_web_api=None,
    )
    client = ScriptedResponsesClient([])
    monkeypatch.setattr(
        'seaqa_ai.agent.agent_service.get_responses_client_by_model_id',
        lambda data_logger, model_id: client,
    )

    AgentService(app, case['project']).run(build_task(case))

    assert seadb.writes('insert') == []
    assert client.calls == []


def test_a_model_failure_marks_the_run_failed_keeps_the_trace_and_raises(monkeypatch):
    monkeypatch.setattr(config, 'COMPLETION_MAX_RETRIES', 0)
    monkeypatch.setattr(config, 'COMPLETION_RETRY_INTERVAL', 0)
    service, task, seadb, _client = _setup(monkeypatch, [
        prelude_output(('ticket', None, TICKET_ID)),
    ])
    # The scripted model has nothing to say for Analysis, so its request fails.

    with pytest.raises(AgentRetryExceededError):
        service.run(task)

    (finish,) = seadb.writes('update', RUNS)[0]
    assert finish['row']['status'] == 'failed'
    assert 'ran out of scripted responses' in finish['row']['error_message']
    assert finish['row']['suggestions_status'] == 'failed'
    # The tracer is flushed even though the run failed, so the UI can show what happened.
    saved = seadb.writes('insert', ACTIONS)[0]
    assert any(row['action_type'] == 'error' for row in saved)
    assert seadb.writes('update', TICKETS) == []
