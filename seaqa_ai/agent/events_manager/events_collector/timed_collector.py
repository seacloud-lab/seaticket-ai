import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from seaqa_ai import config
from seaqa_ai.seadb_models import SchemaTables

from ..constants import (
    EVENT_TYPE_TICKET_DUE_SOON,
    EVENT_TYPE_TICKET_OVER_DUE,
    SOURCE_TYPE_TICKET,
)
from seaqa_ai.agent.models import AgentEvent

logger = logging.getLogger(__name__)


class TimedEventsCollector:
    """Produce due-soon and overdue Ticket events on a fixed schedule."""

    _MIN_REPROCESS_HOURS = 24

    def __init__(self, app):
        self._app = app
        self._should_stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._on_event: Callable[[AgentEvent], bool] | None = None

    def start(self, on_event: Callable[[AgentEvent], bool]) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._should_stop.clear()
        self._on_event = on_event
        self._thread = threading.Thread(
            target=self._scan_loop,
            name='agent_timed_events_collector',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._should_stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _scan_loop(self) -> None:
        while not self._should_stop.is_set():
            try:
                self.scan_once()
            except Exception:
                logger.exception('Timed Ticket scan failed')
            if self._should_stop.wait(config.AGENT_TIMED_SCAN_INTERVAL):
                return

    def scan_once(self) -> None:
        now = datetime.now(timezone.utc)
        for project in self._app.seaqa_db.get_enabled_agent_projects():
            project_uuid = project.get('uuid')
            if not project_uuid or self._is_in_cooldown(project, now):
                continue
            try:
                due_soon_tickets = self._get_due_soon_tickets(project_uuid, project, now)
                overdue_tickets = self._get_overdue_tickets(project_uuid, now)
            except Exception:
                logger.exception('Failed to scan timed Tickets for project %s', project_uuid)
                continue

            submitted = self._submit_events(project_uuid, due_soon_tickets, EVENT_TYPE_TICKET_DUE_SOON, now)
            overdue_submitted = self._submit_events(
                project_uuid,
                overdue_tickets,
                EVENT_TYPE_TICKET_OVER_DUE,
                now,
            )
            submitted = submitted and overdue_submitted
            if submitted:
                self._app.seaqa_db.update_project_agent_scanned_at(project_uuid)

    @staticmethod
    def _is_in_cooldown(project: dict, now: datetime) -> bool:
        last_scanned_at = project.get('last_agent_scanned_at')
        if last_scanned_at is None:
            return False
        if last_scanned_at.tzinfo is None:
            last_scanned_at = last_scanned_at.replace(tzinfo=timezone.utc)
        return now - last_scanned_at < timedelta(seconds=config.AGENT_TIMED_SCAN_PROJECT_COOLDOWN)

    def _submit_events(self, project_uuid: str, tickets: list[dict], event_type: str, now: datetime) -> bool:
        for ticket in tickets:
            ticket_id = ticket.get('_pk')
            if ticket_id is None:
                continue
            event = AgentEvent(
                project_uuid=project_uuid,
                source_type=SOURCE_TYPE_TICKET,
                record_id=int(ticket_id),
                event_type=event_type,
                event={
                    'type': event_type,
                    'old_value': {},
                    'new_value': {},
                },
                connection_id=None,
                modified_time=now,
                received_at=now,
            )
            if not self._on_event or not self._on_event(event):
                return False
        return True

    def _get_due_soon_tickets(self, project_uuid: str, project: dict, now: datetime) -> list[dict]:
        agent_settings = project.get('settings', {}).get('agent', {})
        notify_before_due_hours = int(agent_settings.get('notify_before_due_hours'))
        now_iso = now.isoformat()
        threshold = (now + timedelta(hours=notify_before_due_hours)).isoformat()
        min_reprocess_before = (now - timedelta(hours=self._MIN_REPROCESS_HOURS)).isoformat()
        table_name = SchemaTables.TICKETS.table_name()
        sql = (
            f"SELECT `_pk` FROM `{table_name}` "
            f"WHERE `due_date` IS NOT NULL AND `due_date` >= '{now_iso}' AND `due_date` < '{threshold}' "
            "AND (`state` != 'closed' OR `state` IS NULL) "
            "AND (`deleted` = False OR `deleted` IS NULL) "
            f"AND (`last_agent_processed_at` IS NULL OR `last_agent_processed_at` < '{min_reprocess_before}') "
            "ORDER BY `due_date` ASC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self._app.seadb_service.query_rows(project_uuid, sql).get('results', [])

    def _get_overdue_tickets(self, project_uuid: str, now: datetime) -> list[dict]:
        now_iso = now.isoformat()
        min_reprocess_before = (now - timedelta(hours=self._MIN_REPROCESS_HOURS)).isoformat()
        table_name = SchemaTables.TICKETS.table_name()
        sql = (
            f"SELECT `_pk` FROM `{table_name}` "
            f"WHERE `due_date` IS NOT NULL AND `due_date` < '{now_iso}' "
            "AND (`state` != 'closed' OR `state` IS NULL) "
            "AND (`deleted` = False OR `deleted` IS NULL) "
            f"AND (`last_agent_processed_at` IS NULL OR `last_agent_processed_at` < '{min_reprocess_before}') "
            "ORDER BY `due_date` ASC "
            f"LIMIT {config.AGENT_MAX_TICKETS_PER_RUN}"
        )
        return self._app.seadb_service.query_rows(project_uuid, sql).get('results', [])
