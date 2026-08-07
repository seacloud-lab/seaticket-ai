import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from seaqa_ai.agent.tasks_manager.constants import AGENT_TASK_SCHEMA_VERSION
from seaqa_ai.agent.models import AgentEvent, AgentTask


class EventRouter:
    """Turn an accepted event into a task without reading business data."""

    def route(self, event: AgentEvent) -> AgentTask:
        return AgentTask(
            schema_version=AGENT_TASK_SCHEMA_VERSION,
            task_id=str(uuid4()),
            idempotency_key=self._build_idempotency_key(event),
            project_uuid=event.project_uuid,
            target_source_type=event.source_type,
            target_record_id=event.record_id,
            target_connection_id=event.connection_id,
            trigger_event=event,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _build_idempotency_key(event: AgentEvent) -> str:
        payload = {
            'project_uuid': event.project_uuid,
            'source_type': event.source_type,
            'connection_id': event.connection_id,
            'record_id': event.record_id,
            'event_type': event.event_type,
            'event': event.event,
            'modified_time': event.modified_time.isoformat() if event.modified_time else None,
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
