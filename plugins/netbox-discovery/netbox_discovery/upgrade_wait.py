"""Read-only explanations of the queue gates, calculated when a page is viewed."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from . import upgrade_queue as queue
from .models import UpgradeGroup, UpgradeJob
from .upgrade_choices import ACTIVE


def waiting_details(job, user, now=None):
    if job.status != 'pending':
        return None
    now = now or timezone.now()

    def result(code, label, message, blockers=None):
        # A user may view this job without being allowed to view its blocker.
        visible = []
        if blockers is not None and user is not None:
            visible = list(blockers.restrict(user, 'view').order_by('pk'))
        return {'code': code, 'label': label, 'message': message, 'jobs': visible}

    if job.start_before <= now:
        return result('expired', 'Start window missed', 'The poller will mark this job expired on its next check-in.')
    if job.scheduled_at > now:
        return result('scheduled', 'Waiting for scheduled start', 'This job is not due yet. See its scheduled start time.')

    active = UpgradeJob.objects.filter(status__in=ACTIVE)
    same_device = active.filter(device_id=job.device_id)
    if same_device.exists():
        return result('device_busy', 'Waiting for device', 'Another job still owns this device.', same_device)
    if queue.hold_reason(job):
        return result('hold', 'Waiting for review',
                      'A related job failed or requires recovery. The poller will hold this job for review.')
    for name in job.groups:
        group = UpgradeGroup.objects.filter(name=name).first()
        limit = group.max_concurrent if group is not None else 1
        blockers = active.filter(groups__contains=[name]).exclude(device_id=job.device_id)
        if limit and blockers.count() >= limit:
            return result('group_capacity', 'Waiting for group capacity',
                          f'Group {name} permits {limit} active job(s) at a time.', blockers)
    if not queue.dependencies_done(job):
        blockers = UpgradeJob.objects.filter(batch_id=job.batch_id, device_id__in=job.waits_for).exclude(
            status__in=('completed', 'completed_with_warnings', 'cancelled', 'expired'))
        return result('dependency', 'Waiting for dependency',
                      'Downstream jobs in this batch must finish first.', blockers)
    if not queue.exclusion_clear(job):
        blockers = active.filter(Q(device_id__in=job.waits_for) | Q(waits_for__contains=[job.device_id]))
        return result('dependency', 'Waiting for dependency',
                      'A dependent device is still busy. These jobs cannot run at the same time.', blockers)
    last_seen = job.poller.upgrade_last_seen_at
    if last_seen is None or last_seen < now - timedelta(minutes=5):
        return result('poller_stale', 'Waiting for poller check-in',
                      f'Poller {job.poller.name} has not checked in within the last five minutes.')
    return result('pickup', 'Awaiting pickup',
                  f'No device or dependency gate is blocking this job. Waiting for poller {job.poller.name} '
                  'to claim it when a worker slot is available.')
