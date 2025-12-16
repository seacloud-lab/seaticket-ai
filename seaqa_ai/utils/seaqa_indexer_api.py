import time
import jwt
import requests
import logging

from urllib.parse import urljoin


logger = logging.getLogger(__name__)


class SeaQAIndexerAPI:
    def __init__(self, server_url, secret_key, timeout=60):
        self.server_url = server_url.rstrip('/')
        self.secret_key = secret_key
        self.timeout = timeout

    def gen_headers(self):
        payload = {'exp': int(time.time()) + 300, }
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        return {"Authorization": "Token %s" % token}

    def keywords_search(self, query, project_uuid, connection_ids=None, count=20):
        """
        keywords_search: Uses full-text indexing for searching (searching for the title and content of records). Generally used for searching documents (site & seafile).
        """
        params = {
            'query': query,
            'project_uuid': project_uuid,
            'connection_ids': connection_ids,
            'extra_sources': ['knowledge_base'],
            'search_type': 'semantic_search',
            'count': count
        }
        payload = {'exp': int(time.time()) + 300, }
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        headers = {"Authorization": "Token %s" % token}
        url = urljoin(self.server_url, '/search')
        resp = requests.post(url, json=params, headers=headers)
        if resp.status_code == 500:
            raise Exception('search error status: %s body: %s', resp.status_code, resp.text)
        resp_json = resp.json()
        results = resp_json.get('results', [])
        return results
    
    def vector_search(self, query_vector, project_uuid, connection_ids=None, count=20):
        """
        vector_search: Performs vector analysis on the "AI_SUMMARY" field of a record to find matching related records. It's typically used to find related Issues (github_issue, discourse_forum, email).
        """
        params = {
            'query_vector': query_vector,
            'project_uuid': project_uuid,
            'connection_ids': connection_ids,
            'count': count
        }
        payload = {'exp': int(time.time()) + 300, }
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        headers = {"Authorization": "Token %s" % token}
        url = urljoin(self.server_url, '/summary_vector_search')
        resp = requests.post(url, json=params, headers=headers)
        if resp.status_code == 500:
            raise Exception('search error status: %s body: %s', resp.status_code, resp.text)
        resp_json = resp.json()
        results = resp_json.get('results', [])
        return results
