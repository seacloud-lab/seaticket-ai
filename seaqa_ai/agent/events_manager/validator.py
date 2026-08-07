from datetime import datetime, timedelta, timezone
from typing import Any

from seaqa_ai.utils import uuid_str_to_32_chars

from .constants import (
    CONNECTION_REQUIRED_SOURCE_TYPES,
    EVENT_TYPE_GENERAL_TASK_UPDATED,
    EVENT_TYPE_GITHUB_ISSUE_COMMENT_ADDED,
    MAX_EVENT_AGE_DAYS,
    SUPPORTED_EVENT_TYPES,
    SUPPORTED_SOURCE_TYPES,
)
from seaqa_ai.agent.models import AgentEvent


class EventValidationError(ValueError):
    pass


class EventsValidator:
    """Validate event transport data without loading business records."""

    def validate(self, data: dict[str, Any]) -> AgentEvent:
        if not isinstance(data, dict):
            raise EventValidationError('event payload must be an object')

        event = data.get('event')
        if not isinstance(event, dict):
            raise EventValidationError('event must be an object')
        event = dict(event)

        event_type = event.get('type')
        if event_type not in SUPPORTED_EVENT_TYPES:
            raise EventValidationError(f'unsupported event_type: {event_type}')

        source_type = data.get('source_type')
        if source_type not in SUPPORTED_SOURCE_TYPES:
            raise EventValidationError(f'unsupported source_type: {source_type}')

        project_uuid = uuid_str_to_32_chars(str(data.get('project_uuid') or '').strip())
        if not project_uuid:
            raise EventValidationError('project_uuid is required')

        record_id = self._parse_int(data.get('record_id'), 'record_id')
        connection_id = data.get('connection_id')
        if connection_id is None or connection_id == '':
            if source_type in CONNECTION_REQUIRED_SOURCE_TYPES:
                raise EventValidationError('connection_id is required')
            connection_id = None
        else:
            connection_id = self._parse_int(connection_id, 'connection_id')

        event['old_value'] = self._as_dict(event.get('old_value'))
        event['new_value'] = self._as_dict(event.get('new_value'))
        modified_time = self._parse_modified_time(data.get('modified_time'))

        return AgentEvent(
            project_uuid=project_uuid,
            source_type=source_type,
            record_id=record_id,
            event_type=event_type,
            event=event,
            connection_id=connection_id,
            modified_time=modified_time,
            received_at=datetime.now(timezone.utc),
            event_id=self._optional_text(data.get('event_id')),
            correlation_id=self._optional_text(data.get('correlation_id')),
            causation_id=self._optional_text(data.get('causation_id')),
        )

    @staticmethod
    def _parse_int(value: Any, field_name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise EventValidationError(f'{field_name} must be an integer') from error

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        value = str(value).strip() if value is not None else ''
        return value or None

    @staticmethod
    def _parse_modified_time(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            modified_time = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as error:
            raise EventValidationError('modified_time must be ISO-8601') from error
        if modified_time.tzinfo is None:
            modified_time = modified_time.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - modified_time > timedelta(days=MAX_EVENT_AGE_DAYS):
            raise EventValidationError('event is older than the accepted age')
        return modified_time


class EventsFilter:
    """Stateless no-op filtering retained from the previous event manager."""

    def should_process(self, event: AgentEvent) -> bool:
        if event.event_type == EVENT_TYPE_GENERAL_TASK_UPDATED:
            old_status = str(event.event['old_value'].get('status') or '').strip().lower()
            new_status = str(event.event['new_value'].get('status') or '').strip().lower()
            return old_status != 'done' and new_status == 'done'
        if event.source_type == 'github_issue' and event.event_type == EVENT_TYPE_GITHUB_ISSUE_COMMENT_ADDED:
            author = str(event.event['new_value'].get('author') or '').strip().lower()
            return not author.endswith('[bot]')
        return True
