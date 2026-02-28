import json
import logging
from sqlalchemy import text

from seaqa_ai import config
from seaqa_ai.db import init_db_session_class
from seaqa_ai.agent.agent_worker import AgentWorker
from seaqa_ai.utils import uuid_str_to_32_chars

logger = logging.getLogger(__name__)

class AgentManager:
    """
    Coordinates agent runs across all projects.

    Responsibilities:
    1. Triggered periodically by the scheduler
    2. Queries MySQL for all projects that have agent enabled (settings.agent.enabled=true)
    3. Creates an AgentWorker for each project and runs it
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

    def run_single_project(self, project_uuid):
        """Manually trigger the agent for a single project.

        Returns:
            int | None: created run_id when success, otherwise None.
        """
        session = self.db_session_class()

        try:
            result = session.execute(
                text(
                    "SELECT uuid, settings FROM projects "
                    "WHERE uuid = :uuid AND deleted = 0"
                ),
                {'uuid': uuid_str_to_32_chars(project_uuid)}
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

        try:
            worker = AgentWorker(self.app, project)
            run_id = worker.run()
            return run_id
        except Exception as e:
            logger.exception(f'Agent run failed for project {project_uuid}: {e}')
            return None
