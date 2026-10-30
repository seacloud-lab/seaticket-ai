import json

import pytest

from seaqa_ai.agent.utils.react.tracer import AgentReACTTracer


class FakeSeaDB:
    def __init__(self, pks=None, error=None):
        self.pks = pks
        self.error = error
        self.inserted = []

    def insert_rows(self, project_uuid, table_name, rows):
        if self.error:
            raise self.error
        self.inserted.append((project_uuid, table_name, rows))
        return {'pks': self.pks if self.pks is not None else list(range(100, 100 + len(rows)))}


def _tracer(seadb):
    return AgentReACTTracer('proj', seadb)


def _record_suggestion(tracer, status='pending', run_id=7):
    return tracer.record(
        context={'run_id': run_id},
        phase='handling',
        action_type='suggestion',
        action_status=status,
        step=1,
        tool_name='suggest_reply',
        tool_arguments={'reply': 'hi'},
        suggestion_reason='because',
        suggestion_content='hi',
        suggestion_payload={'k': 'v'},
        target_item_type='github_issue',
        target_item_id='1_101',
        target_item_title='T',
        statistics={'input_tokens': 3},
    )


def test_flushing_nothing_succeeds_without_touching_seadb():
    seadb = FakeSeaDB()

    result = _tracer(seadb).flush()

    assert result == {
        'succeeded': True, 'pending_suggestion_action_ids': [], 'suggestions_status': 'none',
    }
    assert seadb.inserted == []


def test_flush_writes_one_batch_and_reports_pending_suggestions():
    seadb = FakeSeaDB(pks=[11, 12])
    tracer = _tracer(seadb)
    tracer.record(
        context={'run_id': 7}, phase='analysis', action_type='analysis',
        action_status='completed', step=0, result={'analysis_report': 'r'},
    )
    _record_suggestion(tracer)

    result = tracer.flush()

    assert result == {
        'succeeded': True, 'pending_suggestion_action_ids': [12], 'suggestions_status': 'pending',
    }
    (project_uuid, table_name, rows), = seadb.inserted
    assert (project_uuid, table_name) == ('proj', 'agent_actions')
    assert [row['action_type'] for row in rows] == ['analysis', 'suggestion']
    suggestion = rows[1]
    assert suggestion['run_id'] == 7
    assert suggestion['status'] == 'pending'
    assert json.loads(suggestion['suggestion_payload']) == {'k': 'v'}
    assert json.loads(suggestion['statistics']) == {'input_tokens': 3}
    assert suggestion['target_item_id'] == '1_101'
    assert len(tracer) == 0


def test_flush_failure_keeps_the_rows_for_a_later_attempt():
    tracer = _tracer(FakeSeaDB(error=RuntimeError('seadb down')))
    _record_suggestion(tracer)

    result = tracer.flush()

    assert result['succeeded'] is False
    assert result['pending_suggestion_action_ids'] == []
    assert len(tracer) == 1


def test_flush_fails_when_inserted_ids_do_not_match_the_rows():
    tracer = _tracer(FakeSeaDB(pks=[1]))
    _record_suggestion(tracer)
    _record_suggestion(tracer)

    assert tracer.flush()['succeeded'] is False


@pytest.mark.parametrize('statuses, expected', [
    ([], 'none'),
    (['pending'], 'pending'),
    (['executing', 'failed'], 'pending'),
    (['failed', 'executed'], 'failed'),
    (['executed', 'cancelled'], 'resolved'),
])
def test_suggestions_status_summarises_all_suggestion_rows(statuses, expected):
    rows = [{'action_type': 'suggestion', 'status': status} for status in statuses]
    rows.append({'action_type': 'tool_call', 'status': 'completed'})

    assert AgentReACTTracer._calculate_suggestions_status(rows) == expected
