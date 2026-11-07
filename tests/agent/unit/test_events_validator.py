from datetime import datetime, timedelta, timezone

import pytest

from seaqa_ai.agent.events_manager.validator import EventsValidator, EventValidationError

PROJECT = '12345678-1234-5678-1234-567812345678'


def _payload(**overrides):
    payload = {
        'project_uuid': PROJECT,
        'source_type': 'github_issue',
        'record_id': 101,
        'connection_id': 3,
        'event': {'type': 'github_issue_added', 'new_value': {'title': 'T'}},
        'modified_time': datetime.now(timezone.utc).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_valid_event_is_normalised():
    event = EventsValidator().validate(_payload(record_id='101', connection_id='3'))

    assert event.project_uuid == '12345678123456781234567812345678'
    assert (event.source_type, event.record_id, event.connection_id) == ('github_issue', 101, 3)
    assert event.event_type == 'github_issue_added'
    assert event.event['old_value'] == {}
    assert event.event['new_value'] == {'title': 'T'}
    assert event.modified_time.tzinfo is not None


def test_ticket_and_portal_events_need_no_connection():
    for source_type, event_type in [
        ('ticket', 'ticket_over_due'), ('portal_issue', 'portal_issue_added'),
    ]:
        event = EventsValidator().validate(_payload(
            source_type=source_type, connection_id=None,
            event={'type': event_type},
        ))
        assert event.connection_id is None


@pytest.mark.parametrize('overrides, message', [
    ({'event': 'not an object'}, 'event must be an object'),
    ({'event': {'type': 'ticket_added'}}, 'unsupported event_type'),
    ({'source_type': 'slack'}, 'unsupported source_type'),
    ({'project_uuid': ''}, 'project_uuid is required'),
    ({'record_id': 'abc'}, 'record_id must be an integer'),
    ({'connection_id': None}, 'connection_id is required'),
    ({'connection_id': 'x'}, 'connection_id must be an integer'),
    ({'modified_time': 'yesterday'}, 'modified_time must be ISO-8601'),
    (
        {'modified_time': (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()},
        'older than the accepted age',
    ),
])
def test_invalid_payload_is_rejected(overrides, message):
    with pytest.raises(EventValidationError, match=message):
        EventsValidator().validate(_payload(**overrides))


def test_payload_must_be_an_object():
    with pytest.raises(EventValidationError, match='payload must be an object'):
        EventsValidator().validate(['not', 'an', 'object'])


def test_naive_modified_time_is_read_as_utc():
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    event = EventsValidator().validate(_payload(modified_time=naive))

    assert event.modified_time.utcoffset() == timedelta(0)
