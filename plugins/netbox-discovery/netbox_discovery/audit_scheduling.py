"""Dispatch due occurrences using the same permission-checked queue as manual audits."""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from dcim.filtersets import DeviceFilterSet
from dcim.models import Device

from .models import AuditRun, AuditSchedule, JobProfile, UpgradeJob
from . import upgrade_queue as queue
from .upgrade_choices import ACTIVE, WAITING

logger = logging.getLogger(__name__)


def next_occurrence(schedule, after):
    """Keep local wall time; use the first fall-back fold and shift spring gaps forward."""
    if schedule.frequency == 'now':
        return after
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


def payload(schedule, now):
    data = {'operation': 'remediate' if schedule.remediate else 'audit_config', 'filters': {'status': ['active'], **schedule.filters},
            'profile_source': schedule.profile_source,
            'scheduled_at': now, 'start_before': now + timedelta(hours=schedule.window_hours),
            'description': f'Standards schedule: {schedule.name}'}
    if schedule.profile_source == 'custom':
        data['profile'] = schedule.profile
    elif schedule.profile_source == 'saved':
        data['saved_profile'] = schedule.saved_profile_id
    return data


def validate_profile_access(schedule, user):
    """Validate a saved reference without requiring any devices to match today."""
    if schedule.remediate and (not user or not user.has_perm('netbox_discovery.apply_upgradejob')):
        raise queue.QueueError('Audit and remediate requires permission to apply device changes.')
    if schedule.profile_source == 'saved' and not JobProfile.objects.restrict(user, 'view').filter(
            pk=schedule.saved_profile_id, kind='remediate').exists():
        raise queue.QueueError('Choose an accessible saved remediation profile.')


def eligible_devices(schedule, user, *, exclusions=None):
    """Resolve a recurring scope before applying the one-off queue's batch limit."""
    validate_profile_access(schedule, user)
    selection = DeviceFilterSet({'status': ['active'], **schedule.filters},
                                queryset=Device.objects.restrict(user, 'view'))
    if not selection.is_valid():
        raise queue.QueueError(str(selection.errors))
    devices = queue.execution_devices(user, selection.qs)
    if any(device.status != 'active' for device in devices):
        raise queue.QueueError('A selected stack master is not active. Correct its status before scheduling.')
    # Resolve stacks first: an unaddressed member can still be covered by its
    # master, but an unaddressed execution target must never get a device job.
    addressed = [device for device in devices if device.primary_ip4_id or device.primary_ip6_id]
    missing_ip = len(devices) - len(addressed)
    devices = addressed
    missing_profile = 0
    if schedule.profile_source in ('model', 'saved'):
        from .profile_assignments import default_profile_ids
        scoped = devices
        defaults = default_profile_ids(user, scoped, 'remediate')
        matching = [device.pk for device in scoped if device.pk in defaults and (
            schedule.profile_source != 'saved' or defaults[device.pk] == schedule.saved_profile_id)]
        missing_profile = len(scoped) - len(matching)
    else:
        matching = [device.pk for device in devices]
    if exclusions is not None:
        exclusions.update(missing_ip=missing_ip, missing_profile=missing_profile)
    return matching, missing_ip + missing_profile


def check_run_as(schedule):
    run_as = schedule.run_as
    if not run_as or not run_as.is_active or not run_as.has_perm('netbox_discovery.add_upgradejob'):
        raise queue.QueueError('The run-as user is inactive, missing, or cannot schedule audit jobs.')
    if not AuditSchedule.objects.restrict(run_as, 'view').filter(pk=schedule.pk).exists():
        raise queue.QueueError('The run-as user no longer has access to this schedule.')


@transaction.atomic
def dispatch(schedule_id, now=None):
    now = now or timezone.now()
    schedule = AuditSchedule.objects.select_for_update(of=('self',)).select_related('run_as').filter(pk=schedule_id).first()
    if not schedule or not schedule.enabled or not schedule.next_run_at or schedule.next_run_at > now:
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
                exclusions = {}
                device_ids, _ = eligible_devices(schedule, schedule.run_as, exclusions=exclusions)
                # Savepoint rolls back the entire batch if any device is invalid or unauthorized.
                with transaction.atomic():
                    count = 0
                    data = payload(schedule, now)
                    # Keep the one-off queue's guardrail, while allowing a region
                    # to contain more than 1,000 devices in a single occurrence.
                    for offset in range(0, len(device_ids), 1000):
                        jobs = queue.schedule(schedule.run_as, {**data, 'filters': {'id': device_ids[offset:offset + 1000]}})
                        UpgradeJob.objects.filter(pk__in=[job.pk for job in jobs]).update(audit_run=run)
                        count += len(jobs)
                run.outcome = 'queued' if count else 'skipped'
                run.job_count = count
                kind = 'audit and remediation' if schedule.remediate else 'read-only audit'
                run.message = (f'{count} {kind} jobs queued.' if count else
                               'No eligible active devices match this schedule.')
                if exclusions['missing_ip']:
                    run.message += f' {exclusions["missing_ip"]} execution devices without a primary management IP excluded.'
                if exclusions['missing_profile']:
                    run.message += f' {exclusions["missing_profile"]} devices without a matching accessible model or platform profile excluded.'
            except (queue.QueueError, ValidationError) as exc:
                run.outcome = 'failed'
                run.message = str(exc)[:1000]
        run.save()
    # At most one catch-up batch after downtime; never replay every missed day.
    updates = {'last_run_at': now, 'next_run_at': next_occurrence(schedule, now)}
    if schedule.frequency == 'now':
        updates.update(enabled=False, next_run_at=None)
    AuditSchedule.objects.filter(pk=schedule.pk).update(**updates)
    return run


def dispatch_now(schedule_id):
    """Dispatch after commit; unexpected failures remain due for the scheduler's retry."""
    try:
        dispatch(schedule_id)
    except Exception:
        logger.exception('Unable to dispatch immediate standards schedule %s', schedule_id)


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
