import json
from datetime import datetime
from typing import Any

from seaqa_ai.agent.models import AgentEvent, AgentTask


class AgentTaskSerializer:
    @classmethod
    def serialize(cls, task: AgentTask) -> str:
        return json.dumps(cls.to_dict(task), ensure_ascii=False, separators=(',', ':'))

    @classmethod
    def to_dict(cls, task: AgentTask) -> dict[str, Any]:
        event = task.trigger_event
        return {
            'schema_version': task.schema_version,
            'task_id': task.task_id,
            'project_uuid': task.project_uuid,
            'target': {
                'source_type': task.target_source_type,
                'record_id': task.target_record_id,
                'connection_id': task.target_connection_id,
            },
            'trigger_event': {
                'source_type': event.source_type,
                'record_id': event.record_id,
                'connection_id': event.connection_id,
                'event': event.event,
                'modified_time': cls._datetime_to_string(event.modified_time),
                'received_at': cls._datetime_to_string(event.received_at),
            },
            'created_at': cls._datetime_to_string(task.created_at),
        }

    @staticmethod
    def _datetime_to_string(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @classmethod
    def deserialize(cls, value: str | bytes) -> AgentTask:
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        data = json.loads(value)
        target = data['target']
        trigger = data['trigger_event']
        event = AgentEvent(
            project_uuid=data['project_uuid'],
            source_type=trigger['source_type'],
            record_id=int(trigger['record_id']),
            event_type=trigger['event']['type'],
            event=dict(trigger['event']),
            connection_id=trigger.get('connection_id'),
            modified_time=cls._parse_datetime(trigger.get('modified_time')),
            received_at=cls._parse_datetime(trigger['received_at']),
        )
        return AgentTask(
            schema_version=int(data['schema_version']),
            task_id=data['task_id'],
            project_uuid=data['project_uuid'],
            target_source_type=target['source_type'],
            target_record_id=int(target['record_id']),
            target_connection_id=target.get('connection_id'),
            trigger_event=event,
            created_at=cls._parse_datetime(data['created_at']),
        )

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None
