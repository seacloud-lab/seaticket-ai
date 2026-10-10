import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from . import knowledge_gaps

logger = logging.getLogger(__name__)

MAX_WORKERS = 2
MAX_PENDING = 8


class AnalysisService(object):
    def __init__(self, app):
        self._app = app
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='analysis')
        self._pending = set()
        self._lock = Lock()

    def run_knowledge_gaps(self, project_uuid):
        with self._lock:
            if project_uuid in self._pending:
                return {'skipped': True}
            if len(self._pending) >= MAX_PENDING:
                return None
            self._pending.add(project_uuid)
        self._executor.submit(self._run_knowledge_gaps, project_uuid)
        return {'success': True}

    def _run_knowledge_gaps(self, project_uuid):
        try:
            knowledge_gaps.analyze(self._app, project_uuid)
        except Exception:
            logger.exception('Knowledge gap analysis failed for project %s', project_uuid)
        finally:
            with self._lock:
                self._pending.discard(project_uuid)
