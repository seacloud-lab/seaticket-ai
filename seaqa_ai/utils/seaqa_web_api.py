import time
import jwt
import requests
import logging

from urllib.parse import urljoin


logger = logging.getLogger(__name__)


class SeaQAWebAPI:
    """Client for calling seaqa-web internal APIs."""

    def __init__(self, server_url, secret_key, timeout=60):
        self.server_url = server_url.rstrip('/')
        self.secret_key = secret_key
        self.timeout = timeout

    def gen_headers(self):
        """Generate Authorization header with JWT token."""
        payload = {'exp': int(time.time()) + 300}
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        return {"Authorization": "Token %s" % token}

    def execute_auto_action(self, project_uuid, action_id):
        """
        Trigger auto-execution of an agent action in seaqa-web.

        Args:
            project_uuid: The project UUID
            action_id: The action ID to execute

        Returns:
            dict with keys: success, action_id, status, result

        Raises:
            Exception: On HTTP error or unexpected response
        """
        url = urljoin(self.server_url, '/api/v1/internal/agent/auto-action/execute/')
        params = {
            'project_uuid': project_uuid,
            'action_id': action_id,
        }
        headers = self.gen_headers()

        resp = requests.post(url, json=params, headers=headers, timeout=self.timeout)

        if resp.status_code == 403:
            logger.warning('Permission denied calling seaqa-web auto-action endpoint')
            raise Exception('Permission denied: invalid JWT token')

        if resp.status_code == 404:
            logger.warning('Action or project not found for auto-action execution')
            raise Exception('Action or project not found')

        if resp.status_code >= 400:
            logger.warning(
                'Auto-action execution failed: status=%s body=%s',
                resp.status_code,
                resp.text[:500],
            )
            raise Exception(f'Auto-action execution failed: status={resp.status_code}')

        resp_json = resp.json()
        return resp_json
