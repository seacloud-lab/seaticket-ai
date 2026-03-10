import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from seaqa_ai import config
from seaqa_ai.agent.agent_manager import AgentManager
from seaqa_ai.agent.constants import CONFIG_SYNC_INTERVAL_SECONDS

logger = logging.getLogger(__name__)


class AgentScheduler:
    """
    Background scheduler that dynamically manages one APScheduler job per project.

    A lightweight "config-sync" job runs every CONFIG_SYNC_INTERVAL_SECONDS seconds.
    On each sync it reads every project's settings.agent config from MySQL and:
      - Adds a new interval job for projects that became enabled.
      - Reschedules the job when run_interval_hours has changed.
      - Removes the job for projects that became disabled or were deleted.

    Each per-project job calls AgentManager.run(project_uuid).
    """

    def __init__(self, app):
        self.app = app
        self.agent_manager = AgentManager(app)
        self.scheduler = BackgroundScheduler()
        # Tracks the currently-scheduled interval per project so we can detect changes.
        self._project_intervals: dict[str, float] = {}

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _project_job_id(project_uuid: str) -> str:
        return f'agent_project_{project_uuid}'

    def _add_project_job(self, project_uuid: str, interval_hours: float):
        job_id = self._project_job_id(project_uuid)
        self.scheduler.add_job(
            self.agent_manager.run,
            IntervalTrigger(hours=interval_hours),
            id=job_id,
            args=[project_uuid],
            replace_existing=True,
        )
        self._project_intervals[project_uuid] = interval_hours
        logger.info(f'Scheduled agent job for project {project_uuid} (interval: {interval_hours}h)')

    def _remove_project_job(self, project_uuid: str):
        job_id = self._project_job_id(project_uuid)
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f'Removed agent job for project {project_uuid}')
        except Exception:
            pass
        self._project_intervals.pop(project_uuid, None)

    # ── Sync job ───────────────────────────────────────────────────

    def _sync_project_jobs(self):
        """Read all project agent configs from MySQL and reconcile APScheduler jobs."""
        try:
            projects = self.agent_manager.get_all_projects()
        except Exception as e:
            logger.exception(f'Config sync failed - could not fetch projects: {e}')
            return

        enabled_uuids: set[str] = set()

        for project in projects:
            uuid = project['uuid']
            agent_cfg = project.get('settings', {}).get('agent', {})

            if not agent_cfg.get('enabled', False):
                continue

            enabled_uuids.add(uuid)
            interval_hours = float(
                agent_cfg.get('run_interval_hours', config.AGENT_RUN_INTERVAL_HOURS)
            )
            job_id = self._project_job_id(uuid)

            if self.scheduler.get_job(job_id) is None:
                # Project newly enabled – add job.
                self._add_project_job(uuid, interval_hours)
            elif self._project_intervals.get(uuid) != interval_hours:
                # Interval changed – reschedule.
                self.scheduler.reschedule_job(
                    job_id,
                    trigger=IntervalTrigger(hours=interval_hours),
                )
                self._project_intervals[uuid] = interval_hours
                logger.info(
                    f'Rescheduled agent run job for project {uuid} to {interval_hours}h'
                )

        # Remove jobs for projects that are now disabled / deleted.
        for uuid in list(self._project_intervals.keys()):
            if uuid not in enabled_uuids:
                self._remove_project_job(uuid)

    # ── Public API ─────────────────────────────────────────────────

    def start(self):
        self.scheduler.add_job(
            self._sync_project_jobs,
            IntervalTrigger(seconds=CONFIG_SYNC_INTERVAL_SECONDS),
            id='agent_config_sync',
            replace_existing=True,
        )
        self.scheduler.start()
        # Perform an immediate sync so projects start running without waiting
        # for the first CONFIG_SYNC_INTERVAL_SECONDS to elapse.
        self._sync_project_jobs()
        logger.info('Agent scheduler started')

    def stop(self):
        self.scheduler.shutdown(wait=False)
        logger.info('Agent scheduler stopped')
