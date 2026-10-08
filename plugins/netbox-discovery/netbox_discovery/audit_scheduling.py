"""Dispatch due occurrences using the same permission-checked queue as manual audits."""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AuditRun, AuditSchedule, UpgradeJob
from . import upgrade_queue as queue
from .upgrade_choices import ACTIVE, WAITING

logger = logging.getLogger(__name__)


def next_occurrence(schedule, after):
    """Keep local wall time; use the first fall-back fold and shift spring gaps forward."""
    zone = ZoneInfo(schedule.time_zone)
    date = after.astimezone(zone).date()
    for offset in range(9):
        day = date + timedelta(days=offset)
        if schedule.frequency == 'weekly' and day.weekday() != schedule.weekday:
            continue
        wall = datetime.combine(day, schedule.local_time)
        candidates = [wall.replace(tzinfo=zone, fold=fold).astimezone(dt_timezone.utc) for fold in (0, 1)]
        valid = [candidate for candidate in candidates
                 if candidate.astimezone(zone).replace(tzinfo=None) == wall]
        occurrence = min(valid) if valid else max(candidates)
        if occurrence > after:
            return occurrence
    raise ValueError('No next audit occurrence found.')


def payload(schedule, now, user=None):
    data = {'operation': 'audit_config', 'filters': {'status': ['active'], **schedule.filters},
            'profile_source': schedule.profile_source,
            'scheduled_at': now, 'start_before': now + timedelta(hours=schedule.window_hours),
            'description': f'Recurring audit: {schedule.name}'}
    if schedule.profile_source == 'custom':
        data['profile'] = schedule.profile
    elif schedule.profile_source == 'saved':
        data['saved_profile'] = schedule.saved_profile_id
    if user is not None:
        devices = queue.select_devices(user, data['filters'])
        masters = [device.pk for device in devices
                   if not device.virtual_chassis_id or device.virtual_chassis.master_id == device.pk]
        if not masters:
            raise queue.QueueError('No standalone devices or virtual chassis masters match this schedule.')
        data['filters'] = {'id': masters}
    return data


def check_run_as(schedule):
    run_as = schedule.run_as
    if not run_as or not run_as.is_active or not run_as.has_perm('netbox_discovery.add_upgradejob'):
        raise queue.QueueError('The run-as user is inactive, missing, or cannot schedule audit jobs.')
    if not AuditSchedule.objects.restrict(run_as, 'view').filter(pk=schedule.pk).exists():
        raise queue.QueueError('The run-as user no longer has access to this schedule.')


@transaction.atomic
def dispatch(schedule_id, now=None):
    now = now or timezone.now()
    schedule = AuditSchedule.objects.select_for_update(of=('self',)).select_related('run_as').get(pk=schedule_id)
    if not schedule.enabled or not schedule.next_run_at or schedule.next_run_at > now:
        return None
    occurrence = schedule.next_run_at
    run, created = AuditRun.objects.get_or_create(schedule=schedule, scheduled_for=occurrence,
                                                defaults={'dispatched_at': now, 'outcome': 'failed'})
    if created:
        # Expire unclaimed work even when its poller is offline. Active work stays
        # blocking until a worker or operator resolves it, avoiding unsafe overlap.
        UpgradeJob.objects.filter(audit_run__schedule=schedule, status__in=WAITING,
                                  start_before__lte=now).update(
            status='expired', completed_at=now, message='Start window missed; no work dispatched.')
        if UpgradeJob.objects.filter(audit_run__schedule=schedule, status__in=(*ACTIVE, *WAITING)).exists():
            run.outcome = 'skipped'
            run.message = 'Previous audit jobs are still queued or active.'
        else:
            try:
                check_run_as(schedule)
                schedule.full_clean()
                # Savepoint rolls back the entire batch if any device is invalid or unauthorized.
                with transaction.atomic():
                    jobs = queue.schedule(schedule.run_as, payload(schedule, now, schedule.run_as))
                    UpgradeJob.objects.filter(pk__in=[job.pk for job in jobs]).update(audit_run=run)
                run.outcome = 'queued'
                run.job_count = len(jobs)
                run.message = f'{len(jobs)} read-only audit jobs queued.'
            except (queue.QueueError, ValidationError) as exc:
                run.outcome = 'failed'
                run.message = str(exc)[:1000]
        run.save()
    # At most one catch-up batch after downtime; never replay every missed day.
    AuditSchedule.objects.filter(pk=schedule.pk).update(last_run_at=now, next_run_at=next_occurrence(schedule, now))
    return run


def run_due():
    now = timezone.now()
    UpgradeJob.objects.filter(audit_run__isnull=False, status__in=WAITING, start_before__lte=now).update(
        status='expired', completed_at=now, message='Start window missed; no work dispatched.')
    count = 0
    for pk in AuditSchedule.objects.filter(enabled=True, next_run_at__lte=now).values_list('pk', flat=True):
        try:
            count += dispatch(pk, now) is not None
        except Exception:
            # Unexpected failures roll back the occurrence and remain due for retry.
            logger.exception('Unable to dispatch audit schedule %s', pk)
    return count
