import time
import jwt
import logging
import requests
from seaqa_ai.utils import uuid_str_to_36_chars
from seaqa_ai.utils.constants import ConnectionType, ExtraSourceType, TICKET_TABLES

logger = logging.getLogger(__name__)

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
    comments_table_name = ''

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
    def __init__(self, secret_key, server_url, timeout=30):
        self.timeout = timeout
        self.secret_key = secret_key
        self.server_url = server_url

    def gen_headers(self, base_id):
        payload = {
            'exp': int(time.time()) + 3600,
            'base_id': base_id
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

    def get_record_and_comments(self, base_id, record_ids, connection_table_name, comments_table_name=None, topic_id_field_name=None, comments_has_deleted_field=True, record_fields_str='*', comments_field_str='*', comments_topic_id_field_name=None):
        """
        return {
            'record_id': {
                'record': record,
                'comments': comments # if available
            }
        }
        """
        if record_fields_str != '*':
            if '_pk' not in record_fields_str:
                record_fields_str = '_pk, ' + record_fields_str
            if comments_table_name:
                if topic_id_field_name not in record_fields_str:
                    record_fields_str = topic_id_field_name + ', ' + record_fields_str
        if comments_table_name:
            if not comments_topic_id_field_name:
                comments_topic_id_field_name = topic_id_field_name
            if comments_field_str != '*' and comments_topic_id_field_name not in comments_field_str:
                comments_field_str = comments_topic_id_field_name + ', ' + comments_field_str

        condition_str = ", ".join([
            str(record_id) if not isinstance(record_id, str) else f'"{record_id}"'
            for record_id in record_ids
        ])
        records = self.query_rows(base_id, f'SELECT {record_fields_str} FROM {connection_table_name} WHERE _pk IN ({condition_str}) AND (`deleted` = False OR `deleted` IS NULL)')['results']
        record_id_comments_map = {}
        if comments_table_name:
            topic_id_record_id_map = {
                record.get(topic_id_field_name): record['_pk']
                for record in records
            }
            condition_str = ", ".join([
                str(topic_id) if not isinstance(topic_id, str) else f'"{topic_id}"'
                for topic_id in topic_id_record_id_map
            ])
            if not comments_has_deleted_field:
                comments = self.query_rows(base_id, f'SELECT {comments_field_str} FROM {comments_table_name} WHERE {comments_topic_id_field_name} IN ({condition_str})')['results']
            else:
                comments = self.query_rows(base_id, f'SELECT {comments_field_str} FROM {comments_table_name} WHERE {comments_topic_id_field_name} IN ({condition_str}) AND (`deleted` = False OR `deleted` IS NULL)')['results']
            for comment in comments:
                record_id = topic_id_record_id_map[comment.get(comments_topic_id_field_name)]
                if record_id not in record_id_comments_map:
                    record_id_comments_map[record_id] = []
                record_id_comments_map[record_id].append(comment)

        results = {}
        for record in records:
            results[record['_pk']] = {'record': record}
            if record['_pk'] in record_id_comments_map:
                results[record['_pk']]['comments'] = record_id_comments_map[record['_pk']]
        
        return results
