import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

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
        self._stopped = Event()

    def run_knowledge_gaps(self, project_uuid):
        with self._lock:
            if self._stopped.is_set():
                return {'stopped': True}
            if project_uuid in self._pending:
                return {'skipped': True}
            if len(self._pending) >= MAX_PENDING:
                return None
            self._pending.add(project_uuid)
        try:
            self._executor.submit(self._run_knowledge_gaps, project_uuid)
        except RuntimeError:
            with self._lock:
                self._pending.discard(project_uuid)
            return {'stopped': True}
        return {'success': True}

    def stop(self):
        self._stopped.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        for thread in list(self._executor._threads):
            thread.join(timeout=2)
        logger.info('Analysis service stopped')

    def _run_knowledge_gaps(self, project_uuid):
        try:
            knowledge_gaps.analyze(self._app, project_uuid, should_stop=self._stopped.is_set)
        except Exception:
            logger.exception('Knowledge gap analysis failed for project %s', project_uuid)
        finally:
            with self._lock:
                self._pending.discard(project_uuid)
