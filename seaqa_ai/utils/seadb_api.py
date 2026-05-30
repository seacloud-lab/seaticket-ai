import time
import jwt
import logging
import requests
from seaqa_ai.utils import uuid_str_to_36_chars
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType, TICKET_TABLES

logger = logging.getLogger(__name__)
SEADB_COMPONENT_USERNAME = 'seaqa-ai'

def parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    else:
        try:
            return response.json()
        except:
            pass

def gen_seadb_table_name(connection_type, connection_id=None):
    connection_table_name = f'{connection_type}_{connection_id}'
    comments_table_name = None

    if connection_type == ExtraSourceType.KNOWLEDGE_BASE.value:
        connection_table_name = 'knowledge_base'
    elif connection_type == ExtraSourceType.TICKET.value:
        connection_table_name = TICKET_TABLES.TICKETS_TABLE.value
        comments_table_name = TICKET_TABLES.TICKET_COMMENTS_TABLE.value
    
    if connection_type == ConnectionType.GITHUB_ISSUE.value:
        comments_table_name = f'{connection_type}_comments_{connection_id}'
    elif connection_type == ConnectionType.DISCOURSE_FORUM.value:
        comments_table_name = f'{connection_type}_replies_{connection_id}'
    elif connection_type == ConnectionType.EMAIL.value:
        comments_table_name = connection_table_name
        connection_table_name = f'{connection_type}_thread_{connection_id}'
    return connection_table_name, comments_table_name



class SeaDBAPI:
    def __init__(self, secret_key, server_url, timeout=30, username=SEADB_COMPONENT_USERNAME):
        self.timeout = timeout
        self.secret_key = secret_key
        self.server_url = server_url
        self.username = username

    def gen_headers(self, base_id):
        payload = {
            'exp': int(time.time()) + 3600,
            'base_id': base_id,
            'username': self.username,
        }
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        return {"Authorization": "Bearer %s" % token}

    def ping(self):
        url = f'{self.server_url}/ping'
        response = requests.get(url, timeout=self.timeout)
        return parse_response(response)
    
    def query_rows(self, base_id, sql, params=[], convert_keys=True):
        base_id = uuid_str_to_36_chars(base_id)
        headers = self.gen_headers(base_id)
        post_data = {
            'sql': sql,
            'convert_keys': convert_keys
        }
        if params:
            post_data['params'] = params
        url = f'{self.server_url}/api/v1/{base_id}/query'
        response = requests.post(url, json=post_data, headers=headers, timeout=self.timeout)
        return parse_response(response)
    
    def insert_rows(self, base_id, table_name, rows):
        base_id = uuid_str_to_36_chars(base_id)
        headers = self.gen_headers(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/rows'
        data = {
            'table_name': str(table_name),
            'rows': rows
        }
        response = requests.post(url, json=data, headers=headers, timeout=self.timeout)
        return parse_response(response)

    def update_rows(self, base_id, table_name, rows):
        base_id = uuid_str_to_36_chars(base_id)
        headers = self.gen_headers(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/rows'
        data = {
            'table_name': table_name,
            'updates': rows
        }
        response = requests.put(url, json=data, headers=headers, timeout=self.timeout)
        return parse_response(response)

    def get_base_metadata(self, base_id):
        base_id = uuid_str_to_36_chars(base_id)
        headers = self.gen_headers(base_id)
        url = f'{self.server_url}/api/v1/{base_id}/metadata'
        response = requests.get(url, headers=headers, timeout=self.timeout)
        return parse_response(response)

    def get_records_by_pks(self, base_id, table_name, pks, fields_str='*'):
        """
        return {
            'pk': record
        }
        """
        if fields_str != '*':
            if '_pk' not in fields_str:
                fields_str = '_pk, ' + fields_str

        condition_str = ", ".join([
            str(pk) if not isinstance(pk, str) else f'"{pk}"'
            for pk in pks
        ])

        records = self.query_rows(
            base_id, 
            f'SELECT {fields_str} FROM {table_name} WHERE _pk IN ({condition_str}) AND (`deleted` = False OR `deleted` IS NULL)'
        )['results']

        return {
            record['_pk']: record
            for record in records
        }

    def get_comments_by_topic_ids(self, base_id, table_name, topic_ids, topic_id_field_name, 
                              fields_str='*', has_deleted_field=True, max_comments_for_each_topic=20):
        """
        return {
            topic_id: [comments]
        }
        """

        # preparing field str
        if fields_str != '*' and topic_id_field_name not in fields_str:
            fields_str = topic_id_field_name + ', ' + fields_str

        # preparing where condition
        condition_str = ", ".join([
            str(topic_id) if not isinstance(topic_id, str) else f'"{topic_id}"'
            for topic_id in topic_ids
        ])

        # preparing sql
        sql = f'SELECT {fields_str} FROM {table_name} WHERE {topic_id_field_name} IN ({condition_str})'

        if has_deleted_field:
            sql += ' AND (`deleted` = False OR `deleted` IS NULL)'
        
        sql += f' LIMIT 0, {max_comments_for_each_topic}'

        comments = self.query_rows(base_id, sql)['results']
        
        # to topic_id map
        result = {}
        for comment in comments:
            topic_id = comment.get(topic_id_field_name)
            if topic_id not in result:
                result[topic_id] = []
            result[topic_id].append(comment)
        
        return result
