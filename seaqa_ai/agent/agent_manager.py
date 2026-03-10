"""
AgentManager: coordinates agent runs across all projects.
"""
import json
import logging
from datetime import datetime, timezone

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.db.models import Project, ProjectConnection
from seaqa_ai.agent.ticket_agent_worker import TicketAgentWorker
from seaqa_ai.agent.github_agent_worker import GithubIssueAgentWorker
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.agent.constants import GITHUB_ISSUE_CONNECTION_TYPE, AGENT_RUNS_TABLE

logger = logging.getLogger(__name__)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class AgentManager:
    """
    Coordinates agent runs across all projects.

    Responsibilities:
    1. Triggered periodically by the scheduler.
    2. Queries MySQL for all projects with agent.enabled=true.
    3. For each project, runs TicketAgentWorker plus one GithubIssueAgentWorker
       per github_issue connection.
    """

    def __init__(self, app):
        self.app = app
        self.db_session_class = init_db_session_class(config.SQLALCHEMY_DATABASE_URI)

    def get_all_projects(self):
        """Query all non-deleted projects with their settings from MySQL.

        Returns:
            list[dict]: Each item has 'uuid' and 'settings' keys.
        """
        session = self.db_session_class()
        try:
            return Project.get_all_non_deleted_projects(session)
        finally:
            session.close()

    def _get_github_issue_connections(self, project_uuid: str) -> list[dict]:
        """Return all active github_issue connections for the given project.

        Returns:
            list[dict]: Each item has at least 'id' and 'type' keys.
        """
        session = self.db_session_class()
        try:
            project_uuid_32 = uuid_str_to_32_chars(project_uuid)
            return ProjectConnection.get_connections_by_project_and_type(
                session, project_uuid_32, GITHUB_ISSUE_CONNECTION_TYPE
            )
        finally:
            session.close()

    def _create_shared_run_record(self, project_uuid: str) -> int:
        row = {
            'status': 'running',
            'started_at': _now_iso(),
            'items_processed': 0,
        }
        res = self.app.seadb_api.insert_rows(project_uuid, AGENT_RUNS_TABLE, [row])
        pks = res.get('pks', [])
        if len(pks) != 1:
            raise Exception(f'Expected 1 PK, got {len(pks)}')
        return pks[0]

    def _update_run_record(self, project_uuid: str, run_pk: int, updates: dict):
        self.app.seadb_api.update_rows(
            project_uuid,
            AGENT_RUNS_TABLE,
            [{'pk': run_pk, 'row': updates}],
        )

    def run_single_project(self, project_uuid: str):
        """Run all workers for a single project.
        """
        session = self.db_session_class()
        try:
            project_uuid_32 = uuid_str_to_32_chars(project_uuid)
            project = Project.get_project_by_uuid(session, project_uuid_32)
            
            if not project:
                logger.warning(f'Project {project_uuid} not found or deleted')
                return None
        except Exception as e:
            logger.exception(f'Failed to query project {project_uuid}: {e}')
            return None
        finally:
            session.close()

        total_items_processed = 0
        worker_errors = []
        run_pk = None

        try:
            run_pk = self._create_shared_run_record(project_uuid)
        except Exception as e:
            logger.exception(f'Failed to create run record for project {project_uuid}: {e}')
            return None

        # 1. Process tickets
        try:
            ticket_worker = TicketAgentWorker(self.app, project)
            ticket_worker.run(run_pk=run_pk)
            total_items_processed += ticket_worker.last_items_processed
            if ticket_worker.last_run_failed and ticket_worker.last_run_error_message:
                worker_errors.append(f'ticket: {ticket_worker.last_run_error_message}')
        except Exception as e:
            logger.exception(f'TicketAgentWorker failed for project {project_uuid}: {e}')
            worker_errors.append(f'ticket: {str(e)[:300]}')

        # 2. Process GitHub Issues
        connections = self._get_github_issue_connections(project_uuid)
        for connection in connections:
            try:
                github_worker = GithubIssueAgentWorker(self.app, project, connection)
                github_worker.run(run_pk=run_pk)
                total_items_processed += github_worker.last_items_processed
                if github_worker.last_run_failed and github_worker.last_run_error_message:
                    worker_errors.append(
                        f'github_connection_{connection["id"]}: '
                        f'{github_worker.last_run_error_message}'
                    )
            except Exception as e:
                logger.exception(
                    f'GithubIssueAgentWorker failed for project {project_uuid}, '
                    f'connection {connection["id"]}: {e}'
                )
                worker_errors.append(
                    f'github_connection_{connection["id"]}: {str(e)[:300]}'
                )

        final_status = 'failed' if worker_errors else 'completed'
        error_message = '; '.join(worker_errors)[:1000] if worker_errors else ''
        try:
            self._update_run_record(project_uuid, run_pk, {
                'status': final_status,
                'finished_at': _now_iso(),
                'items_processed': total_items_processed,
                'error_message': error_message,
            })
        except Exception as e:
            logger.exception(
                f'Failed to finalize run {run_pk} for project {project_uuid}: {e}'
            )

        return run_pk
