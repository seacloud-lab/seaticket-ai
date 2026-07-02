import logging
from collections.abc import Iterator

from sqlalchemy import text
from seaqa_ai.utils import uuid_str_to_32_chars
from seaqa_ai.db.models import Project

logger = logging.getLogger('__name__')


class SeaqaDB(object):
    def __init__(self, db_session_class):
        self.db_session_class = db_session_class

    def get_project_settings_by_project_uuid(self, project_uuid):
        project_uuid = uuid_str_to_32_chars(project_uuid)
        with self.db_session_class() as session:
            sql = "SELECT settings FROM projects WHERE uuid=:project_uuid AND deleted=false"
            return session.execute(
                text(sql),
                {'project_uuid': project_uuid}
            ).scalar_one_or_none()

    @staticmethod
    def is_agent_enabled(project: dict) -> bool:
        if not isinstance(project, dict):
            return False
        settings = project.get('settings')
        if not isinstance(settings, dict):
            return False
        agent_settings = settings.get('agent')
        if not isinstance(agent_settings, dict):
            return True
        return bool(agent_settings.get('enabled'))
    
    def get_project_by_uuid(self, project_uuid: str) -> dict | None:
        with self.db_session_class() as db_session:
            try:
                project_uuid_32 = uuid_str_to_32_chars(project_uuid)
                return Project.get_project_by_uuid(db_session, project_uuid_32)
            except Exception as e:
                logger.exception(f'Failed to query project {project_uuid}: {e}')
        return None
    
    def get_all_projects(self, page_size: int = 50) -> Iterator[dict]:
        with self.db_session_class() as db_session:
            try:
                page = 1
                while True:
                    projects = Project.get_all_active_projects(db_session, page=page, page_size=page_size)
                    if not projects:
                        break
                    for project in projects:
                        yield project
                    if len(projects) < page_size:
                        break
                    page += 1
            except Exception as e:
                logger.exception(f'Failed to get projects: {e}')

    def get_enabled_agent_projects(self) -> Iterator[dict]:
        for project in self.get_all_projects():
            if self.is_agent_enabled(project):
                yield project

    def update_project_agent_scanned_at(self, project_uuid: str) -> bool:
        project_uuid = uuid_str_to_32_chars(project_uuid)
        if not project_uuid:
            return False
        with self.db_session_class() as db_session: 
            return Project.update_last_agent_scanned_at(db_session, project_uuid)
