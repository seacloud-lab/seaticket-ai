from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    """A validated event received from an agent event source."""

    project_uuid: str
    source_type: str
    record_id: int
    event_type: str
    event: dict[str, Any]
    connection_id: int | None
    modified_time: datetime | None
    received_at: datetime


@dataclass(frozen=True)
class AgentTask:
    """A versioned task ready for asynchronous Agent execution."""

    schema_version: int
    task_id: str
    project_uuid: str
    trigger_event: AgentEvent
    created_at: datetime
