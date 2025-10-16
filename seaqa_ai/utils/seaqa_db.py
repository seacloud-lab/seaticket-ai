import logging
from sqlalchemy import text


logger = logging.getLogger('__name__')


class SeaqaDB(object):
    def __init__(self, db_session_class):
        self.db_session_class = db_session_class
        
    def get_connection_type_pj_uuid_by_id(self, connection_id):
        with self.db_session_class() as session:
            sql = """ SELECT pc.type, pc.project_uuid FROM project_connection pc WHERE pc.id=:connection_id """
            row = session.execute(text(sql), {'connection_id': connection_id}).first()
            return row.type, row.project_uuid