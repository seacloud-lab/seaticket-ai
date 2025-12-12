import time
import jwt
import logging
import requests

logger = logging.getLogger(__name__)

def parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    else:
        try:
            return response.json()
        except:
            pass


class SeaDBAPI:
    def __init__(self, secret_key, server_url, username='', timeout=30):
        self.timeout = timeout
        self.secret_key = secret_key
        self.server_url = server_url
        self.username = username

    def gen_headers(self, base_id):
        payload = {
            'exp': int(time.time()) + 3600,
            'base_id': base_id,
            'username': self.username
        }
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        return {"Authorization": "Bearer %s" % token}

    def ping(self):
        url = f'{self.server_url}/ping'
        response = requests.get(url, timeout=self.timeout)
        return parse_response(response)
    
    def query_rows(self, base_id, sql, params=[], convert_keys=True):
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
    
    def get_record_and_comments(self, base_id, record_id, connection_table_name, comments_table_name=None, topic_id_field_name=None, has_deleted_field=False):
        record = self.query_rows(base_id, f'SELECT * FROM {connection_table_name} WHERE _pk = {record_id}')
        if not record['results']:
            return None, None
        record = record['results'][0]
        if not comments_table_name:
            return record, None
        if not has_deleted_field:
            comments = self.query_rows(base_id, f'SELECT * FROM {comments_table_name} WHERE {topic_id_field_name} = {record.get(topic_id_field_name)}')
        else:
            comments = self.query_rows(base_id, f'SELECT * FROM {comments_table_name} WHERE {topic_id_field_name} = {record.get(topic_id_field_name)} and deleted = 0')
        return record, comments['results']
