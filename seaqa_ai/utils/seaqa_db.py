from sqlalchemy import text
from seaqa_ai.utils import uuid_str_to_32_chars


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
