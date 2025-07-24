import time
import jwt
import requests
import logging

from urllib.parse import urljoin


logger = logging.getLogger(__name__)


class SeaQAIndexAPI:
    def __init__(self, app, server_url, secret_key, timeout=60):
        self.app = app
        self.server_url = server_url.rstrip('/')
        self.secret_key = secret_key
        self.timeout = timeout

    def gen_headers(self):
        payload = {'exp': int(time.time()) + 300, }
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        return {"Authorization": "Token %s" % token}

    def search(self, query, project_uuid, connection_type):
        params = {
            'query': query,
            'project_uuid': project_uuid,
            'connection_type': connection_type
        }
        payload = {'exp': int(time.time()) + 300, }
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        headers = {"Authorization": "Token %s" % token}
        url = urljoin(self.server_url, '/search')
        resp = requests.post(url, json=params, headers=headers)
        if resp.status_code == 500:
            raise Exception('search error status: %s body: %s', resp.status_code, resp.text)
        resp_json = resp.json()
        results = resp_json.get('results')
        return results
