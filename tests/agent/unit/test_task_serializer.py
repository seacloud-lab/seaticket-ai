from datetime import datetime, timezone

import pytest

from seaqa_ai.agent.models import AgentEvent, AgentTask
from seaqa_ai.agent.tasks_manager.serializer import AgentTaskSerializer


def _task(modified_time=None):
    now = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
    event = AgentEvent(
        project_uuid='a' * 32,
        source_type='email',
        record_id=42,
        event_type='email_thread_added',
        event={'type': 'email_thread_added', 'old_value': {}, 'new_value': {'title': '标题'}},
        connection_id=3,
        modified_time=modified_time,
        received_at=now,
    )
    return AgentTask(
        schema_version=1, task_id='t-1', project_uuid='a' * 32,
        trigger_event=event, created_at=now,
    )


@pytest.mark.parametrize('modified_time', [None, datetime(2026, 10, 22, 7, 0, tzinfo=timezone.utc)])
def test_round_trip_keeps_every_field(modified_time):
    task = _task(modified_time)

    restored = AgentTaskSerializer.deserialize(AgentTaskSerializer.serialize(task))

    assert restored == task


def test_bytes_payload_is_accepted():
    task = _task()

    restored = AgentTaskSerializer.deserialize(AgentTaskSerializer.serialize(task).encode('utf-8'))

    assert restored == task


def test_serialized_form_keeps_non_ascii_text_readable():
    assert '标题' in AgentTaskSerializer.serialize(_task())


@pytest.mark.parametrize('payload', ['not json', '{}', '{"trigger_event": {}}'])
def test_malformed_payload_raises_what_the_worker_drops(payload):
    with pytest.raises((ValueError, TypeError, KeyError)):
        AgentTaskSerializer.deserialize(payload)
