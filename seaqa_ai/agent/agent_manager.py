"""
AgentManager: coordinates agent runs across all projects.
"""
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import text

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.agent.ticket_agent_worker import TicketAgentWorker
from seaqa_ai.agent.github_agent_worker import GithubIssueAgentWorker
from seaqa_ai.utils import uuid_str_to_32_chars

logger = logging.getLogger(__name__)

GITHUB_ISSUE_CONNECTION_TYPE = 'github_issue'
AGENT_RUNS_TABLE = 'agent_runs'


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
            result = session.execute(
                text("SELECT uuid, settings FROM projects WHERE deleted = 0")
            )
            rows = result.fetchall()
            projects = []
            for row in rows:
                try:
                    settings = json.loads(row[1] or '{}')
                except (json.JSONDecodeError, TypeError):
                    settings = {}
                projects.append({
                    'uuid': str(row[0]),
                    'settings': settings,
                })
            return projects
        except Exception as e:
            logger.exception(f'Failed to query projects: {e}')
            return []
        finally:
            session.close()

    def _get_github_issue_connections(self, project_uuid: str) -> list[dict]:
        """Return all active github_issue connections for the given project.

        Returns:
            list[dict]: Each item has at least 'id' and 'type' keys.
        """
        session = self.db_session_class()
        try:
            result = session.execute(
                text(
                    "SELECT id, type, name FROM project_connection "
                    "WHERE project_uuid = :uuid AND type = :conn_type AND deleted = 0"
                ),
                {
                    'uuid': uuid_str_to_32_chars(project_uuid),
                    'conn_type': GITHUB_ISSUE_CONNECTION_TYPE,
                },
            )
            rows = result.fetchall()
            return [{'id': row[0], 'type': row[1], 'name': row[2]} for row in rows]
        except Exception as e:
            logger.exception(
                f'Failed to query github_issue connections for project {project_uuid}: {e}'
            )
            return []
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

    def run_all_projects(self):
        """Run agent for all projects that have agent.enabled=true."""
        projects = self.get_all_projects()
        for project in projects:
            agent_cfg = project.get('settings', {}).get('agent', {})
            if not agent_cfg.get('enabled', False):
                continue
            try:
                self.run_single_project(project['uuid'])
            except Exception as e:
                logger.exception(f'Error running agent for project {project["uuid"]}: {e}')

    def run_single_project(self, project_uuid: str):
        """Run all workers for a single project.
        """
        session = self.db_session_class()
        try:
            result = session.execute(
                text(
                    "SELECT uuid, settings FROM projects "
                    "WHERE uuid = :uuid AND deleted = 0"
                ),
                {'uuid': uuid_str_to_32_chars(project_uuid)},
            )
            row = result.fetchone()
            if not row:
                logger.warning(f'Project {project_uuid} not found or deleted')
                return None

            settings_str = row[1] or '{}'
            try:
                settings = json.loads(settings_str)
            except (json.JSONDecodeError, TypeError):
                settings = {}

            project = {
                'uuid': str(row[0]),
                'settings': settings,
            }
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
