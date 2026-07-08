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

    def execute_auto_actions(self, project_uuid, action_ids):
        """
        Trigger auto-execution of one or more agent actions in seaqa-web in a
        single request.

        Args:
            project_uuid: The project UUID
            action_ids: List of action IDs to execute

        Returns:
            dict with keys: success, results (per-action outcome list)

        Raises:
            Exception: On HTTP error or unexpected response
        """
        url = urljoin(self.server_url, '/api/v1/internal/agent/auto-action/execute/')
        params = {
            'project_uuid': project_uuid,
            'action_ids': list(action_ids),
        }
        headers = self.gen_headers()

        resp = requests.post(url, json=params, headers=headers, timeout=self.timeout)

        if resp.status_code == 403:
            logger.warning('Permission denied calling seaqa-web auto-action endpoint')
            raise Exception('Permission denied: invalid JWT token')

        if resp.status_code == 404:
            logger.warning('Project not found for auto-action execution')
            raise Exception('Project not found')

        if resp.status_code >= 400:
            logger.warning(
                'Auto-action execution failed: status=%s body=%s',
                resp.status_code,
                resp.text[:500],
            )
            raise Exception(f'Auto-action execution failed: status={resp.status_code}')

        resp_json = resp.json()
        return resp_json
