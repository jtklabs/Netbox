"""Explicitly confirmed boot-volume recovery, dispatched through the owning poller."""
import re
from copy import deepcopy
from datetime import timedelta

from dcim.models import Device
from django.db import transaction
from django.utils import timezone

from .models import UpgradeJob
from .upgrade_choices import ACTIVE, WAITING
from . import upgrade_queue as queue

PERMISSIONS = tuple('netbox_discovery.' + action + '_upgradejob' for action in ('view', 'change', 'add', 'apply'))


def plan_for(source):
    if source.operation != 'upgrade' or not str(source.profile.get('image', '')).lower().endswith('.iso'):
        raise queue.QueueError('Boot partition rollback is available only for F5 upgrade jobs.')
    if not source.started_at:
        raise queue.QueueError('This job never authorized an upgrade; there is no upgrade to roll back.')
    saved = source.summary.get('rollback_plan', {})
    if not isinstance(saved, dict):
        saved = {}
    volume = saved.get('volume') or source.summary.get('rollback_volume')
    if not isinstance(volume, str) or not re.fullmatch(r'HD\d+\.\d+', volume):
        raise queue.QueueError('The old boot partition was not recorded by this job. New upgrades record it with worker 0.2.3 or newer.')
    plan = {'volume': volume, 'expected_version': source.profile['target_version']}
    for key in ('from_volume', 'previous_version', 'previous_build'):
        if saved.get(key):
            plan[key] = saved[key]
    if plan.get('from_volume') and (not isinstance(plan['from_volume'], str) or not re.fullmatch(r'HD\d+\.\d+', plan['from_volume'])):
        raise queue.QueueError('The upgraded boot partition recorded by this job is invalid.')
    if plan.get('from_volume') == volume:
        raise queue.QueueError('The recorded old and upgraded boot partitions are identical.')
    if source.summary.get('rollback_note'):
        plan['note'] = source.summary['rollback_note']
    return plan


def stopped(source):
    return (source.status not in ('pending', 'held', 'claimed', 'running')
            and not source.cancellation_pending and bool(source.completed_at))


def check_source(source):
    plan = plan_for(source)
    if not stopped(source):
        raise queue.QueueError('Cancel the running upgrade and wait for its worker to confirm it stopped before rolling back.')
    queue.check_target(source)
    if UpgradeJob.objects.filter(device_id=source.device_id, operation='upgrade',
                                 started_at__gt=source.started_at).exclude(pk=source.pk).exists():
        raise queue.QueueError('A newer upgrade has started on this device. Use that job for rollback.')
    return plan


def context(source, user):
    if source.operation != 'upgrade' or not str(source.profile.get('image', '')).lower().endswith('.iso'):
        return None
    if not user.has_perms(PERMISSIONS) or not UpgradeJob.objects.restrict(user, 'change').restrict(user, 'apply').filter(pk=source.pk).exists():
        return None
    previous = source.rollback_jobs.restrict(user, 'view').order_by('-pk').first()
    try:
        plan = check_source(source)
        return {'plan': plan, 'previous': previous}
    except queue.QueueError as exc:
        return {'error': str(exc), 'previous': previous}


@transaction.atomic
def request(user, pk, device_name):
    if not user.has_perms(PERMISSIONS):
        raise queue.QueueError('Rollback requires view, change, add and apply device-job permissions.')
    source = UpgradeJob.objects.restrict(user, 'view').restrict(user, 'change').restrict(user, 'apply').select_for_update().get(pk=pk)
    if device_name != source.device_name:
        raise queue.QueueError('Type the exact device name to confirm the immediate reboot without traffic draining.')
    # Serialize duplicate confirmations and all claims for this device.
    device = Device.objects.restrict(user, 'view').select_for_update().filter(pk=source.device_id).first()
    if device is None:
        raise queue.QueueError('The source device is outside your view permissions.')
    source.device = device
    plan = check_source(source)
    previous = source.rollback_jobs.order_by('-pk').first()
    if previous and previous.status not in ('failed', 'expired', 'cancelled'):
        if not UpgradeJob.objects.restrict(user, 'view').filter(pk=previous.pk).exists():
            raise queue.QueueError('A rollback job already exists for this upgrade.')
        return previous
    if UpgradeJob.objects.filter(device_id=device.pk, status__in=ACTIVE + WAITING).exclude(pk=source.pk).exists():
        raise queue.QueueError('Another queued or active job reserves this device. Close it before requesting rollback.')
    now = timezone.now()
    job = UpgradeJob(device=device, device_name=source.device_name, address=source.address, poller=source.poller,
                     operation='f5_rollback', rollback_of=source, rollback_plan=plan,
                     profile=deepcopy(source.profile), profile_name=source.profile_name,
                     scheduled_at=now, start_before=now + timedelta(minutes=15), requested_by=user,
                     groups=source.groups, waits_for=source.waits_for,
                     message='Confirmed immediate reboot without traffic draining; awaiting a rollback-capable poller.',
                     description=f'Boot partition rollback of job {source.pk}')
    job.full_clean()
    job.save()
    if not UpgradeJob.objects.restrict(user, 'add').restrict(user, 'apply').filter(pk=job.pk).exists():
        raise queue.QueueError('Rollback is outside your schedule or apply permissions.')
    return job


def transfer_reservation(source, job):
    """Called with source and device locked, immediately before claiming rollback."""
    check_source(source)
    if source.status == 'recovery_required':
        source.status = 'cancelled'
        source.claim_token = None
        source.message = f'Recovery reservation transferred to rollback job {job.pk}.'
        source.save()
