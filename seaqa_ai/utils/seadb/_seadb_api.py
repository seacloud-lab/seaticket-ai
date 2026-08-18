import base64
import logging

import requests

from seaqa_ai.utils import uuid_str_to_36_chars

logger = logging.getLogger(__name__)


def _parse_response(response):
    if response.status_code >= 400 or response.status_code < 200:
        raise ConnectionError(response.status_code, response.text)
    try:
        return response.json()
    except ValueError:
        return None


class SeaDBAPI:
    """HTTP client for SeaDB's base-level API."""

    def __init__(self, server_url, seadb_user, seadb_passwd, timeout=30):
        self.timeout = timeout
        self.server_url = server_url
        self.seadb_user = seadb_user
        self.seadb_passwd = seadb_passwd
        self.headers = None
        self.gen_headers()

    def gen_headers(self):
        auth_str = f'{self.seadb_user}:{self.seadb_passwd}'
        b64 = base64.b64encode(auth_str.encode('utf-8')).decode()
        self.headers = {'Authorization': f'Basic {b64}'}

    def ping(self):
        response = requests.get(f'{self.server_url}/ping', timeout=self.timeout)
        return _parse_response(response)

    def query_rows(self, base_id, sql, params=None, convert_keys=True):
        base_id = uuid_str_to_36_chars(base_id)
        data = {'sql': sql, 'convert_keys': convert_keys}
        if params:
            data['params'] = params
        response = requests.post(
            f'{self.server_url}/api/v1/{base_id}/query',
            json=data,
            headers=self.headers,
            timeout=self.timeout,
        )
        return _parse_response(response)

    def insert_rows(self, base_id, table_name, rows):
        base_id = uuid_str_to_36_chars(base_id)
        response = requests.post(
            f'{self.server_url}/api/v1/{base_id}/rows',
            json={'table_name': str(table_name), 'rows': rows},
            headers=self.headers,
            timeout=self.timeout,
        )
        return _parse_response(response)

    def update_rows(self, base_id, table_name, rows):
        base_id = uuid_str_to_36_chars(base_id)
        response = requests.put(
            f'{self.server_url}/api/v1/{base_id}/rows',
            json={'table_name': table_name, 'updates': rows},
            headers=self.headers,
            timeout=self.timeout,
        )
        return _parse_response(response)

    def get_base_metadata(self, base_id):
        base_id = uuid_str_to_36_chars(base_id)
        response = requests.get(
            f'{self.server_url}/api/v1/{base_id}/metadata',
            headers=self.headers,
            timeout=self.timeout,
        )
        return _parse_response(response)
