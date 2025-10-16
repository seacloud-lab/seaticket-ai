import logging

from seaqa_ai.utils.seadb_api import SeaDBAPI
from seaqa_ai.seadb_models.models import DiscourseTopicsTable, DiscourseRepliesTable


logger = logging.getLogger(__name__)

class DiscourseSeaDBAPI:
    def __init__(self, base_id, username='', timeout=30):
        self.base_id = base_id
        self.seadb_api = SeaDBAPI(username=username, timeout=timeout)

    def get_topics_by_connection_id(self, connection_id, topic_id=None):
        """Retrieve all topics for the specified connection_id."""
        table_name = DiscourseTopicsTable.gen_table_name(connection_id)
        sql = f"SELECT * FROM `{table_name}`"
        if topic_id is not None:
            if 'WHERE' in sql:
                sql += f" AND `topic_id` = {topic_id}"
            else:
                sql += f" WHERE `topic_id` = {topic_id}"
        result = self.seadb_api.query_rows(self.base_id, sql)
        if result and 'results' in result:
            return result['results']
        return []

    def get_replies_by_topic(self, connection_id, topic_id):
        """Retrieve all replies for the specified topic."""
        table_name = DiscourseRepliesTable.gen_table_name(connection_id)
        sql = f"SELECT * FROM `{table_name}` WHERE `topic_id` = {topic_id}"
        result = self.seadb_api.query_rows(self.base_id, sql)
        if result and 'results' in result:
            return result['results']
        return []
