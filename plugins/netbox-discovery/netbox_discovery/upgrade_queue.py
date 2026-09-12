"""Transactional pull queue. A lost worker is never automatically replayed."""
import ipaddress
import json
import uuid
from datetime import timedelta

from dcim.filtersets import DeviceFilterSet
from dcim.models import Device
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import DiscoveryPoller, UpgradeJob
from .upgrade_choices import ACTIVE, TERMINAL
from .utils import plugin_setting


class QueueError(ValueError):
    pass


def owners(device):
    """Device tags override site tags, then the nearest tagged ancestor region.

    More than one tag is allowed, but requires a chosen poller when scheduling.
    An upgrade always runs through exactly one poller.
    """
    prefix = plugin_setting('poller_tag_prefix').lower()
    def names(obj):
        return {t.slug.lower()[len(prefix):] for t in obj.tags.all()
                if t.slug.lower().startswith(prefix)} if obj else set()
    found = names(device) or names(device.site)
    region, seen = device.site.region if device.site else None, set()
    while not found and region and region.pk not in seen:
        seen.add(region.pk)
        found = names(region)
        region = region.parent
    return found


def address_of(device):
    primary = device.primary_ip4 or device.primary_ip6
    if not primary:
        raise QueueError(f'{device}: no primary management IP')
    return str(ipaddress.ip_interface(str(primary.address)).ip)


def validate_profile(profile):
    # The remote uses the full IOS XE Profile validator before connecting.
    # Reject scripts, credential fields and malformed structures at intake too.
    keys = {'name', 'models', 'starting_versions', 'target_version', 'image', 'md5',
            'minimum_free_bytes', 'bundle_conversion_validated', 'image_source',
            # BIG-IP profiles; the worker's validator applies the family rules.
            'volume', 'allow_active', 'ucs_backup', 'license_check_date'}
    required = keys - {'bundle_conversion_validated', 'image_source', 'volume', 'allow_active', 'ucs_backup', 'license_check_date'}
    if not isinstance(profile, dict) or set(profile) - keys or required - set(profile):
        raise QueueError('Provide a complete upgrade profile with only supported profile fields.')
    if len(json.dumps(profile)) > 16000:
        raise QueueError('Upgrade profile is too large.')
    for key in ('models', 'starting_versions'):
        if not isinstance(profile[key], list) or not profile[key] or not all(isinstance(x, str) for x in profile[key]):
            raise QueueError(f'Profile {key} must be a nonempty list of strings.')
    for key in ('name', 'target_version', 'image', 'md5'):
        if not isinstance(profile[key], str) or not profile[key].strip():
            raise QueueError(f'Profile {key} must be a nonempty string.')


def select_devices(user, filters):
    if not isinstance(filters, dict) or not filters:
        raise QueueError('Choose at least one device filter; an unfiltered fleet is not allowed.')
    for name, value in filters.items():
        values = value if isinstance(value, list) else [value]
        if not values or any(type(v) not in (str, int, bool) or (isinstance(v, str) and not v.strip()) for v in values):
            raise QueueError(f'Device filter {name} is empty or malformed; do not silently expand the selection.')
    # Same filterset as /api/dcim/devices/: site, role, platform, tag, IDs, etc.
    allowed = set(DeviceFilterSet.base_filters)
    if set(filters) - allowed:
        raise QueueError(f'Unknown device filters: {sorted(set(filters) - allowed)}')
    selected = DeviceFilterSet(filters, queryset=Device.objects.restrict(user, 'view'))
    if not selected.is_valid():
        raise QueueError(str(selected.errors))
    devices = list(selected.qs.select_related('site__region', 'primary_ip4', 'primary_ip6',
                                              'virtual_chassis').prefetch_related('tags')[:1001])
    if not devices or len(devices) > 1000:
        raise QueueError('Selection must contain between 1 and 1000 devices.')
    return devices


def prepare(user, data):
    validate_profile(data['profile'])
    if data['operation'] not in {'audit', 'stage', 'upgrade'}:
        raise QueueError('Unsupported upgrade operation.')
    if data['operation'] != 'audit' and not user.has_perm('netbox_discovery.apply_upgradejob'):
        raise QueueError('Scheduling changes requires apply permission on upgrade jobs.')
    if not timezone.is_aware(data['scheduled_at']) or not timezone.is_aware(data['start_before']):
        raise QueueError('Schedule timestamps must include a time zone.')
    if data['start_before'] <= max(timezone.now(), data['scheduled_at']):
        raise QueueError('Start-before must be in the future and after the scheduled start.')
    preferred = (data.get('poller') or '').lower().removeprefix(plugin_setting('poller_tag_prefix'))
    rows = []
    for device in select_devices(user, data['filters']):
        if device.virtual_chassis_id and device.virtual_chassis.master_id != device.pk:
            raise QueueError(f'{device}: select only the virtual chassis master (one job per stack).')
        candidates = owners(device)
        chosen = preferred if preferred in candidates else next(iter(candidates)) if len(candidates) == 1 and not preferred else None
        if not chosen:
            raise QueueError(f'{device}: no unambiguous poller; check device/site/region tags or choose a matching poller.')
        poller = DiscoveryPoller.objects.filter(name=chosen).first()
        if poller and poller.tenant_id and poller.tenant_id != device.tenant_id:
            raise QueueError(f'{device}: poller tenant does not match the device.')
        rows.append({'device': device, 'poller_name': chosen, 'address': address_of(device)})
    return rows


@transaction.atomic
def schedule(user, data):
    rows = prepare(user, data)
    batch = uuid.uuid4()
    jobs = []
    for row in rows:
        poller, _ = DiscoveryPoller.objects.get_or_create(name=row['poller_name'])
        job = UpgradeJob(device=row['device'], device_name=row['device'].name,
                         address=row['address'], poller=poller, batch_id=batch,
                         profile=data['profile'], operation=data['operation'],
                         scheduled_at=data['scheduled_at'], start_before=data['start_before'],
                         requested_by=user, description=data.get('description', ''))
        job.full_clean()
        job.save()
        if not UpgradeJob.objects.restrict(user, 'add').filter(pk=job.pk).exists():
            raise QueueError('A selected device is outside your schedule permissions.')
        if job.operation != 'audit' and not UpgradeJob.objects.restrict(user, 'apply').filter(pk=job.pk).exists():
            raise QueueError('A selected device is outside your apply permissions.')
        jobs.append(job)
    return jobs


def check_target(job):
    device = job.device
    if address_of(device) != job.address or device.name != job.device_name:
        raise QueueError('Device name or primary IP changed after scheduling; create a new schedule.')
    if job.poller.name not in owners(device):
        raise QueueError('Poller ownership changed after scheduling; create a new schedule.')
    if job.poller.tenant_id and job.poller.tenant_id != device.tenant_id:
        raise QueueError('Poller tenant no longer matches the device.')
    if device.virtual_chassis_id and device.virtual_chassis.master_id != device.pk:
        raise QueueError('Device is no longer the virtual chassis master.')


@transaction.atomic
def edit_pending(user, pk, data):
    # Serialize edits with claims so a worker's captured assignment cannot change.
    job = UpgradeJob.objects.restrict(user, 'change').select_for_update().get(pk=pk)
    if job.status != 'pending':
        raise QueueError('Only pending jobs can be edited. This job has already been claimed or closed.')
    if data['last_updated'] != job.last_updated:
        raise QueueError('This job changed while the form was open. Reload the page before editing again.')
    if job.operation != 'audit' and not UpgradeJob.objects.restrict(user, 'apply').filter(pk=pk).exists():
        raise QueueError('Editing staged-image or upgrade jobs requires apply permission.')
    check_target(job)
    # Reuse scheduling validation and device visibility without retargeting the job.
    prepare(user, {**data, 'filters': {'id': [job.device_id]}, 'poller': job.poller.name})
    job.snapshot()
    for field in ('operation', 'scheduled_at', 'start_before', 'profile', 'description'):
        setattr(job, field, data[field])
    job.full_clean()
    job.save()
    if not UpgradeJob.objects.restrict(user, 'change').filter(pk=pk).exists():
        raise QueueError('The updated job is outside your change permissions.')
    if job.operation != 'audit' and not UpgradeJob.objects.restrict(user, 'apply').filter(pk=pk).exists():
        raise QueueError('The updated job is outside your apply permissions.')
    return job


def expire(queryset, now):
    # Queryset is permission-restricted and poller-scoped by the caller.
    queryset.filter(status='pending', start_before__lte=now).update(
        status='expired', completed_at=now, message='Start window missed; no work dispatched.')
    stale = now - timedelta(minutes=5)
    queryset.filter(status='claimed', last_seen_at__lt=stale).update(
        status='failed', completed_at=now, message='Worker lost before apply authorization; schedule again after checking the poller.')
    queryset.filter(status='running', last_seen_at__lt=stale).update(
        status='recovery_required', message='Worker heartbeat lost after apply authorization. Device remains locked.')


@transaction.atomic
def claim(user, poller, limit, apply):
    now = timezone.now()
    qs = UpgradeJob.objects.restrict(user, 'run').filter(poller=poller)
    expire(qs, now)
    jobs = []
    candidates = qs.filter(status='pending', scheduled_at__lte=now, start_before__gt=now)
    if not apply:
        candidates = candidates.filter(operation='audit')
    # Per-device locks also serialize separate schedules for the same switch.
    # skip_locked keeps an overlapping minute check-in cheap.
    for job in candidates.select_for_update(of=('self',), skip_locked=True).order_by('scheduled_at', 'pk')[:1000]:
        locked = Device.objects.select_for_update(skip_locked=True).filter(pk=job.device_id).first()
        if locked is None or UpgradeJob.objects.filter(device_id=job.device_id, status__in=ACTIVE).exists():
            continue
        try:
            check_target(job)
        except QueueError as exc:
            job.status, job.message, job.completed_at = 'failed', str(exc), now
            job.save()
            continue
        job.claim_token = uuid.uuid4()
        job.status, job.claimed_at, job.last_seen_at = 'claimed', now, now
        job.save()
        jobs.append(job)
        if len(jobs) >= limit:
            break
    return jobs


def assignment(job):
    return {'id': job.pk, 'device_id': job.device_id, 'device': job.device_name,
            'hostname': job.address, 'profile': job.profile, 'operation': job.operation,
            'claim_token': str(job.claim_token), 'batch_id': str(job.batch_id),
            'scheduled_at': job.scheduled_at.isoformat(), 'start_before': job.start_before.isoformat()}


@transaction.atomic
def report(user, pk, data):
    job = UpgradeJob.objects.restrict(user, 'run').select_for_update().get(pk=pk)
    if not job.claim_token or str(job.claim_token) != str(data['claim_token']):
        raise QueueError('Claim token does not match.')
    # Server receive time shows upgrade-worker activity independently of SNMP.
    # Invalid reports roll this back with the surrounding transaction.
    now = timezone.now()
    DiscoveryPoller.objects.filter(pk=job.poller_id).update(last_seen_at=now, upgrade_last_seen_at=now)
    # Terminal event retries acknowledge without changing the completed result.
    if job.status in TERMINAL:
        if data.get('sequence', 0) and data['sequence'] <= job.sequence:
            return job
        raise QueueError('This job has already ended.')
    if data.get('heartbeat'):
        UpgradeJob.objects.filter(pk=pk).update(last_seen_at=now)
        return job
    sequence = data['sequence']
    if sequence <= job.sequence:
        return job
    stage = data['stage']
    if stage == 'ready' and job.started_at is None:
        if job.operation == 'audit':
            raise QueueError('An audit cannot authorize changes.')
        if job.status != 'claimed' or not job.scheduled_at <= now < job.start_before:
            raise QueueError('Start window closed or claim is no longer active.')
        check_target(job)
        job.status, job.started_at = 'running', now
    outcome = {
        'already_current': 'completed', 'dry_run_complete': 'completed', 'staged': 'completed',
        'completed': 'completed', 'completed_with_warnings': 'completed_with_warnings',
        'failed': 'failed', 'blocked': 'failed', 'staging_failed': 'recovery_required',
        'validation_failed': 'recovery_required', 'recovery_required': 'recovery_required',
    }.get(stage)
    if outcome:
        # A post-authorization error must not unlock a potentially changed device.
        if outcome == 'failed' and job.started_at:
            outcome = 'recovery_required'
        job.status = outcome
        job.completed_at = now
    job.sequence, job.stage = sequence, stage
    job.message = data['message']
    job.run_id = data.get('run_id', '') or job.run_id
    job.last_seen_at = now
    job.summary = {**job.summary, **data.get('summary', {})}
    if stage in ('precheck_complete', 'validating') and 'summary' in data:
        job.summary['baseline' if stage == 'precheck_complete' else 'post_validation'] = data['summary']
    event = {k: data[k] for k in ('sequence', 'stage', 'message', 'run_id', 'summary') if k in data}
    event['timestamp'] = now.isoformat()
    # Bounded event history; every event is retained in the remote archive.
    job.events = (job.events + [event])[-500:]
    job.save()
    return job


@transaction.atomic
def cancel(user, pk, reason='', recovered=False):
    job = UpgradeJob.objects.restrict(user, 'change').select_for_update().get(pk=pk)
    if recovered:
        if not job.needs_recovery or not reason.strip():
            raise QueueError('Only recovery-required jobs can be released; describe the verified device state.')
    elif job.status != 'pending':
        raise QueueError('Only a pending job can be cancelled. An active upgrade must finish recovery.')
    job.status, job.completed_at, job.claim_token = 'cancelled', timezone.now(), None
    job.message = reason or 'Cancelled before dispatch.'
    job.save()
    return job
