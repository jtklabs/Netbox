from netbox.jobs import JobRunner
from netbox.constants import ADVISORY_LOCK_KEYS
from django_pglocks import advisory_lock

from netbox_discovery import upgrade_prestage


class AuditScheduleJob(JobRunner):
    class Meta:
        name = 'Recurring configuration audits'

    @classmethod
    @advisory_lock(ADVISORY_LOCK_KEYS['job-schedules'])
    def enqueue_once(cls, *args, **kwargs):
        import django_rq
        from core.choices import JobStatusChoices

        # NetBox's enqueue_once checks PostgreSQL, not Redis. Recover a lost
        # dispatcher after a Redis reset without deleting the audit history.
        for job in cls.get_jobs().filter(status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES):
            queue = django_rq.get_queue(job.queue_name or 'default')
            if queue.fetch_job(str(job.job_id)) is None:
                job.terminate(status=JobStatusChoices.STATUS_ERRORED,
                              error='Dispatcher missing from Redis; recreated at worker startup.')
        return super().enqueue_once(*args, **kwargs)

    def run(self, *args, **kwargs):
        from .audit_scheduling import run_due
        count = run_due()
        self.logger.info('Processed %s due audit schedules.', count)
        return count


class PrestageJob(JobRunner):
    """Recurring image prestage from the enabled prestage policies — Operations > Jobs."""

    class Meta:
        name = 'Automatic image staging'

    def run(self, *args, **kwargs):
        return upgrade_prestage.run(logger=self.logger.info)
