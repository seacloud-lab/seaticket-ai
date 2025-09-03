import logging
from sqlalchemy import text


logger = logging.getLogger('__name__')


class SeaqaDB(object):
    def __init__(self, db_session_class):
        self.db_session_class = db_session_class
        
    def get_connection_type_by_id(self, connection_id):
        with self.db_session_class() as session:
            sql = """ SELECT pc.type FROM project_connection pc WHERE pc.id=:connection_id """
            connectio_type = session.execute(text(sql), {'connection_id': connection_id}).scalar() 
            return connectio_type

    def get_topic_title_by_id(self, connection_id, topic_id):
        with self.db_session_class() as session:
            sql = """
                   SELECT title
                   FROM discourse_topics
                   WHERE connection_id = :connection_id AND topic_id = :topic_id
               """
            row = session.execute(text(sql), {'connection_id': connection_id, 'topic_id': topic_id}).scalar() 
        return row

    def get_replies_for_topic(self, connection_id, topic_id):
        with self.db_session_class() as session:
            sql = """
                   SELECT post_number, content
                   FROM discourse_replies
                   WHERE connection_id = :connection_id AND topic_id = :topic_id
               """
            rows = session.execute(text(sql), {'connection_id': connection_id, 'topic_id': topic_id}).fetchall()
        return rows
