"""Transactional pull queue. A lost worker is never automatically replayed."""
import ipaddress
import json
import uuid
from copy import deepcopy
from datetime import timedelta

from dcim.filtersets import DeviceFilterSet
from dcim.models import Device
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from django.db.models import Q

from .models import DeviceTypeProfile, DiscoveryPoller, JobProfile, UpgradeGroup, UpgradeJob
from .upgrade_choices import (ACTIVE, CONFIG_OPERATIONS, READ_ONLY_OPERATIONS,
                              REMEDIATION_FEATURES, REMEDIATION_MODES, TERMINAL, WAITING)
from .upgrade_groups import GroupError, downstream_of, memberships, plan_waves
from .utils import plugin_setting


class QueueError(ValueError):
    pass


# Operations that take nothing out of service, so one failure does not hold the site.
SITE_HOLD_EXEMPT = ('stage', *CONFIG_OPERATIONS)


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


def validate_profile(profile, operation=None):
    # The remote uses the full IOS XE Profile validator before connecting.
    # Reject scripts, credential fields and malformed structures at intake too.
    # Staging does not depend on the running release, so a staging profile may
    # leave starting_versions empty.
    if operation in CONFIG_OPERATIONS:
        return validate_remediation(profile)
    keys = {'name', 'models', 'starting_versions', 'target_version', 'image', 'md5',
            'minimum_free_bytes', 'bundle_conversion_validated', 'image_source', 'skip_running_config_check',
            # BIG-IP profiles; the worker's validator applies the family rules.
            'volume', 'allow_active', 'ucs_backup', 'license_check_date'}
    required = keys - {'bundle_conversion_validated', 'image_source', 'volume', 'allow_active', 'ucs_backup',
                       'license_check_date', 'skip_running_config_check'}
    if not isinstance(profile, dict) or set(profile) - keys or required - set(profile):
        raise QueueError('Provide a complete upgrade profile with only supported profile fields.')
    if len(json.dumps(profile)) > 16000:
        raise QueueError('Upgrade profile is too large.')
    if type(profile.get('skip_running_config_check', False)) is not bool:
        raise QueueError('Profile skip_running_config_check must be a boolean.')
    for key in ('models', 'starting_versions'):
        optional = key == 'starting_versions' and operation == 'stage'
        if not isinstance(profile[key], list) or not (profile[key] or optional) or not all(isinstance(x, str) for x in profile[key]):
            raise QueueError(f'Profile {key} must be a nonempty list of strings.')
    for key in ('name', 'target_version', 'image', 'md5'):
        if not isinstance(profile[key], str) or not profile[key].strip():
            raise QueueError(f'Profile {key} must be a nonempty string.')


def validate_remediation(profile):
    """Features and a mode; the queue pins the applicable NetBox standards separately."""
    features = {value for value, _ in REMEDIATION_FEATURES}
    if (not isinstance(profile, dict) or not {'features', 'mode'}.issubset(profile) or
            set(profile) - {'features', 'mode', 'allow_clearpass_cluster_changes'}):
        raise QueueError('A remediation names its features, mode and optional ClearPass cluster-change approval.')
    if type(profile.get('allow_clearpass_cluster_changes', False)) is not bool:
        raise QueueError('ClearPass cluster-change approval must be true or false.')
    chosen = profile['features']
    if (not isinstance(chosen, list) or not chosen or not all(isinstance(item, str) for item in chosen)
            or len(set(chosen)) != len(chosen)
            or any(feature not in features for feature in chosen)):
        raise QueueError('Choose at least one remediation feature.')
    if not isinstance(profile['mode'], str) or profile['mode'] not in {value for value, _ in REMEDIATION_MODES}:
        raise QueueError('Remediation mode must be add or replace.')
    if profile.get('allow_clearpass_cluster_changes') and 'ntp' not in chosen:
        raise QueueError('ClearPass cluster-change approval requires the NTP standard.')


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
                                              'virtual_chassis', 'device_type').prefetch_related('tags')[:1001])
    if not devices or len(devices) > 1000:
        raise QueueError('Selection must contain between 1 and 1000 devices.')
    return devices


def resolve_profiles(user, data, devices):
    """Resolve only visible defaults; freeze a separate plan for every device."""
    source = data.get('profile_source') or ('custom' if data.get('profile') is not None else 'model')
    kind = 'remediate' if data['operation'] in CONFIG_OPERATIONS else 'upgrade'
    if source == 'custom':
        validate_profile(data.get('profile'), data['operation'])
        return {device.pk: (deepcopy(data['profile']), '') for device in devices}
    if source not in ('model', 'saved'):
        raise QueueError('Select model defaults, a saved profile, or custom settings.')
    if data.get('profile') is not None:
        raise QueueError('Custom settings cannot be combined with a saved profile or model defaults.')
    profiles = JobProfile.objects.restrict(user, 'view').filter(kind=kind)
    if source == 'saved':
        selected = data.get('saved_profile')
        profile = profiles.filter(pk=getattr(selected, 'pk', selected)).first()
        if profile is None:
            raise QueueError('Choose an accessible saved profile matching this operation.')
        defaults = {device.device_type_id: profile.pk for device in devices}
    else:
        field = 'remediation_profile_id' if kind == 'remediate' else 'upgrade_profile_id'
        defaults = dict(DeviceTypeProfile.objects.restrict(user, 'view').filter(
            device_type_id__in={device.device_type_id for device in devices}).values_list('device_type_id', field))
    available = {profile.pk: profile for profile in profiles.filter(pk__in=set(defaults.values()))}
    resolved = {}
    for device in devices:
        profile = available.get(defaults.get(device.device_type_id))
        if profile is None:
            raise QueueError(f'{device}: no accessible {kind} profile assigned to model {device.device_type}.')
        plan = profile.resolved_plan()
        if kind == 'upgrade' and not DeviceTypeProfile.objects.restrict(user, 'view').filter(
                device_type_id=device.device_type_id, upgrade_profile=profile).exists():
            raise QueueError(f'{device}: profile {profile.name} is not assigned to model {device.device_type}.')
        validate_profile(plan, data['operation'])
        resolved[device.pk] = (plan, profile.name)
    return resolved


def prepare(user, data):
    if data['operation'] not in {'audit', 'stage', 'upgrade', *CONFIG_OPERATIONS}:
        raise QueueError('Unsupported upgrade operation.')
    if data['operation'] not in READ_ONLY_OPERATIONS and not user.has_perm('netbox_discovery.apply_upgradejob'):
        raise QueueError('Scheduling changes requires apply permission on upgrade jobs.')
    if not timezone.is_aware(data['scheduled_at']) or not timezone.is_aware(data['start_before']):
        raise QueueError('Schedule timestamps must include a time zone.')
    if data['start_before'] <= max(timezone.now(), data['scheduled_at']):
        raise QueueError('Start-before must be in the future and after the scheduled start.')
    preferred = (data.get('poller') or '').lower().removeprefix(plugin_setting('poller_tag_prefix'))
    rows = []
    devices = select_devices(user, data['filters'])
    profiles = resolve_profiles(user, data, devices)
    for device in devices:
        if device.virtual_chassis_id and device.virtual_chassis.master_id != device.pk:
            raise QueueError(f'{device}: select only the virtual chassis master (one job per stack).')
        candidates = owners(device)
        chosen = preferred if preferred in candidates else next(iter(candidates)) if len(candidates) == 1 and not preferred else None
        if not chosen:
            raise QueueError(f'{device}: no unambiguous poller; check device/site/region tags or choose a matching poller.')
        poller = DiscoveryPoller.objects.filter(name=chosen).first()
        if poller and poller.tenant_id and poller.tenant_id != device.tenant_id:
            raise QueueError(f'{device}: poller tenant does not match the device.')
        plan, profile_name = profiles[device.pk]
        standards_snapshot = {}
        if data['operation'] in CONFIG_OPERATIONS:
            from django.apps import apps
            if not apps.is_installed('netbox_compliance'):
                raise QueueError('Scheduled remediation requires the Config Compliance plugin and versioned standards.')
            from netbox_compliance.definitions import snapshot_for_device
            from django.core.exceptions import ValidationError
            try:
                standards_snapshot = snapshot_for_device(user, device, plan['features'], plan['mode'],
                                                         audit=data['operation'] == 'audit_config')
            except ValidationError as exc:
                raise QueueError('; '.join(exc.messages)) from None
            if device.platform and device.platform.slug == 'aruba-clearpass' and 'ntp' in plan['features']:
                from .clearpass_cluster import snapshot
                applying = data['operation'] == 'remediate'
                if applying and not plan.get('allow_clearpass_cluster_changes'):
                    raise QueueError('Enable Allow ClearPass cluster-wide changes on the job/profile before scheduling remediation.')
                standards_snapshot['clearpass_cluster'] = snapshot(user, device, apply=applying)
            if device.platform and device.platform.slug == 'arubaos' and 'ntp' in plan['features']:
                from .mobility_conductor import snapshot
                standards_snapshot['mobility_conductor'] = snapshot(user, device)
        rows.append({'device': device, 'poller_name': chosen, 'address': address_of(device),
                     'profile': plan, 'profile_name': profile_name, 'standards_snapshot': standards_snapshot})
    devices = [row['device'] for row in rows]
    # A configuration push takes nothing out of service, so redundancy groups
    # and dependencies do not order it.
    ordered = data['operation'] not in CONFIG_OPERATIONS
    groups = memberships(devices) if ordered else {}
    downstream = downstream_of(devices) if ordered else {}
    for row in rows:
        row['groups'] = groups.get(row['device'].pk, [])
        row['waits_for'] = downstream.get(row['device'].pk, [])
    try:
        plan_waves(rows)
    except GroupError as exc:
        raise QueueError(str(exc)) from None
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
                         profile=row['profile'], profile_name=row['profile_name'], operation=data['operation'],
                         standards_snapshot=row['standards_snapshot'],
                         scheduled_at=data['scheduled_at'], start_before=data['start_before'],
                         requested_by=user, description=data.get('description', ''),
                         groups=row['groups'], waits_for=row['waits_for'], planned_wave=row['wave'])
        job.full_clean()
        job.save()
        if not UpgradeJob.objects.restrict(user, 'add').filter(pk=job.pk).exists():
            raise QueueError('A selected device is outside your schedule permissions.')
        if job.operation not in READ_ONLY_OPERATIONS and not UpgradeJob.objects.restrict(user, 'apply').filter(pk=job.pk).exists():
            raise QueueError('A selected device is outside your apply permissions.')
        jobs.append(job)
    return jobs


def requeue_window(job, now):
    """Start now, keeping the closed job's window length (at least an hour)."""
    return now, now + max(job.start_before - job.scheduled_at, timedelta(hours=1))


@transaction.atomic
def requeue(user, pk):
    """A new job for a closed job's device, profile and operation, due now.

    Nothing about the old outcome carries over: ownership, the profile,
    permissions and the redundancy groups are validated again as a fresh
    single-device schedule in its own batch.
    """
    job = UpgradeJob.objects.restrict(user, 'view').get(pk=pk)
    if job.status not in TERMINAL:
        raise QueueError('Only a closed job can be re-queued; cancel or recover this one first.')
    if UpgradeJob.objects.filter(device_id=job.device_id, status__in=ACTIVE + WAITING).exists():
        raise QueueError(f'{job.device_name} already has a queued or active job.')
    scheduled_at, start_before = requeue_window(job, timezone.now())
    return schedule(user, {'filters': {'id': [job.device_id]}, 'profile': job.profile, 'operation': job.operation,
                           'scheduled_at': scheduled_at, 'start_before': start_before,
                           'poller': job.poller.name, 'description': job.description})[0]


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
    if job.audit_run_id and data['operation'] != 'audit_config':
        raise QueueError('Recurring audit jobs are read-only. Schedule remediation separately.')
    if job.status != 'pending':
        raise QueueError('Only pending jobs can be edited. This job has already been claimed or closed.')
    if data['last_updated'] != job.last_updated:
        raise QueueError('This job changed while the form was open. Reload the page before editing again.')
    if job.operation not in READ_ONLY_OPERATIONS and not UpgradeJob.objects.restrict(user, 'apply').filter(pk=pk).exists():
        raise QueueError('Editing staged-image or upgrade jobs requires apply permission.')
    check_target(job)
    # Reuse scheduling validation and device visibility without retargeting the job.
    rows = prepare(user, {**data, 'filters': {'id': [job.device_id]}, 'poller': job.poller.name})
    job.snapshot()
    job.profile = rows[0]['profile']
    job.profile_name = rows[0]['profile_name']
    job.standards_snapshot = rows[0]['standards_snapshot']
    for field in ('operation', 'scheduled_at', 'start_before', 'description'):
        setattr(job, field, data[field])
    job.full_clean()
    job.save()
    if not UpgradeJob.objects.restrict(user, 'change').filter(pk=pk).exists():
        raise QueueError('The updated job is outside your change permissions.')
    if job.operation not in READ_ONLY_OPERATIONS and not UpgradeJob.objects.restrict(user, 'apply').filter(pk=pk).exists():
        raise QueueError('The updated job is outside your apply permissions.')
    return job


def related_jobs(job):
    """Jobs for this job's partners (shared group) and for the devices it waits for."""
    if not job.groups and not job.waits_for:
        return UpgradeJob.objects.none()
    condition = Q(device_id__in=job.waits_for)
    for name in job.groups:
        condition |= Q(groups__contains=[name])
    return UpgradeJob.objects.filter(condition).exclude(pk=job.pk).exclude(device_id=job.device_id)


def hold_reason(job):
    """Why this job must not start: a fenced partner anywhere, or a failure in its batch.

    Failures a person already acknowledged when releasing this job are not
    reasons again; a new failure still is.
    """
    acknowledged = job.acknowledged or []
    related = related_jobs(job).exclude(pk__in=acknowledged)
    fenced = related.filter(status='recovery_required').first()
    if fenced is not None:
        return f'{fenced.device_name} requires recovery'
    failed = related.filter(batch_id=job.batch_id, status='failed').first()
    if failed is not None:
        return f'{failed.device_name} ended failed in this batch'
    # Site holds guard audits and upgrades: a failed image copy or config push
    # says nothing about whether the next switch at the site can reload safely.
    if plugin_setting('hold_site_on_failure') and job.operation not in SITE_HOLD_EXEMPT:
        at_site = UpgradeJob.objects.filter(batch_id=job.batch_id, status__in=('failed', 'recovery_required'),
                                            device__site_id=job.device.site_id,
                                            ).exclude(operation__in=SITE_HOLD_EXEMPT).exclude(pk=job.pk).exclude(pk__in=acknowledged).first()
        if at_site is not None:
            return f'{at_site.device_name} ended {at_site.status} at the same site in this batch'
    return ''


def capacity_available(job):
    """Every group of the device has room; a limit of 0 means all members may go at once."""
    for name in job.groups:
        group = UpgradeGroup.objects.filter(name=name).first()
        limit = group.max_concurrent if group is not None else 1
        if not limit:
            continue
        active = UpgradeJob.objects.filter(status__in=ACTIVE, groups__contains=[name]).exclude(device_id=job.device_id).count()
        if active >= limit:
            return False
    return True


def exclusion_clear(job):
    """Nothing this job waits for, and nothing that waits for it, is upgrading right now in any batch."""
    if job.waits_for and UpgradeJob.objects.filter(status__in=ACTIVE, device_id__in=job.waits_for).exists():
        return False
    return not UpgradeJob.objects.filter(status__in=ACTIVE, waits_for__contains=[job.device_id]).exclude(device_id=job.device_id).exists()


def dependencies_done(job):
    """Downstream jobs in the same batch have ended; cancelled or expired ones no longer count."""
    if not job.waits_for:
        return True
    return not UpgradeJob.objects.filter(batch_id=job.batch_id, device_id__in=job.waits_for).exclude(
        status__in=('completed', 'completed_with_warnings', 'cancelled', 'expired')).exists()


def dependents_and_partners(job):
    """Pending jobs that share a group with this job's device or wait for it."""
    condition = Q(waits_for__contains=[job.device_id])
    for name in job.groups:
        condition |= Q(groups__contains=[name])
    return UpgradeJob.objects.filter(condition).exclude(pk=job.pk).exclude(device_id=job.device_id)


def hold_related(job):
    """Mark pending partners, dependents and, by default, the rest of the site's batch held when this job fails."""
    reason = f'{job.device_name} ended {job.status}'
    pending = dependents_and_partners(job).filter(status='pending').select_for_update()
    if job.status != 'recovery_required':
        pending = pending.filter(batch_id=job.batch_id)
    held = set()
    for other in pending:
        detail = reason + (' in this batch' if other.batch_id == job.batch_id else '')
        other.status, other.held_reason, other.message = 'held', detail, f'Held: {detail}'
        other.save()
        held.add(other.pk)
    if plugin_setting('hold_site_on_failure') and job.operation not in SITE_HOLD_EXEMPT:
        at_site = UpgradeJob.objects.filter(batch_id=job.batch_id, status='pending',
                                            device__site_id=job.device.site_id).exclude(operation__in=SITE_HOLD_EXEMPT)
        for other in at_site.exclude(pk=job.pk).exclude(pk__in=held).select_for_update():
            detail = f'{reason} at the same site in this batch'
            other.status, other.held_reason, other.message = 'held', detail, f'Held: {detail}'
            other.save()


def expire(queryset, now):
    # Queryset is permission-restricted and poller-scoped by the caller.
    queryset.filter(status__in=('pending', 'held'), start_before__lte=now).update(
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
        candidates = candidates.filter(operation__in=READ_ONLY_OPERATIONS)
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
        reason = hold_reason(job)
        if reason:
            job.status, job.held_reason, job.message = 'held', reason, f'Held: {reason}'
            job.save()
            continue
        if not capacity_available(job) or not dependencies_done(job) or not exclusion_clear(job):
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
            'standards_snapshot': job.standards_snapshot,
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
        if job.operation in READ_ONLY_OPERATIONS:
            raise QueueError('An audit cannot authorize changes.')
        if job.status != 'claimed' or not job.scheduled_at <= now < job.start_before:
            raise QueueError('Start window closed or claim is no longer active.')
        check_target(job)
        if job.operation == 'remediate':
            from netbox_compliance.models import ConfigStandard
            if not job.standards_snapshot:
                raise QueueError('Re-create this job with versioned NetBox standards before applying changes.')
            if (job.standards_snapshot.get('clearpass_cluster') or
                    (job.device.platform and job.device.platform.slug == 'aruba-clearpass' and
                     'ntp' in job.profile.get('features', []))):
                from .clearpass_cluster import snapshot
                pinned_cluster = job.standards_snapshot.get('clearpass_cluster')
                if (not job.profile.get('allow_clearpass_cluster_changes') or not job.requested_by or
                        not pinned_cluster or snapshot(job.requested_by, job.device, apply=True) != pinned_cluster):
                    raise QueueError('ClearPass cluster scope or approval changed; create a new schedule.')
                if data.get('summary', {}).get('clearpass_cluster') != pinned_cluster:
                    raise QueueError('Worker did not acknowledge the approved ClearPass cluster; update the worker.')
            if (job.standards_snapshot.get('mobility_conductor') or
                    (job.device.platform and job.device.platform.slug == 'arubaos' and
                     'ntp' in job.profile.get('features', []))):
                from .mobility_conductor import snapshot
                pinned_conductor = job.standards_snapshot.get('mobility_conductor')
                if (not pinned_conductor or not job.requested_by or
                        not job.device.platform or job.device.platform.slug != 'arubaos' or
                        snapshot(job.requested_by, job.device) != pinned_conductor):
                    raise QueueError('Mobility Conductor assignment changed; create a new schedule.')
                if data.get('summary', {}).get('mobility_conductor') != pinned_conductor:
                    raise QueueError('Worker did not acknowledge the pinned Mobility Conductor; update the worker.')
            for pinned in job.standards_snapshot['revisions']:
                standard = ConfigStandard.objects.filter(pk=pinned['standard_id'], revision=pinned['revision']).first()
                if standard is None or not standard.is_active or not standard.applies_to(job.device):
                    raise QueueError('A standard changed or left scope after scheduling; create a new schedule.')
            expected = [{'standard_id': pinned['standard_id'], 'revision': pinned['revision']}
                        for pinned in job.standards_snapshot['revisions']]
            if data.get('summary', {}).get('standards_revisions') != expected:
                raise QueueError('Worker did not acknowledge the pinned standards; update the worker before applying changes.')
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
    if job.operation in CONFIG_OPERATIONS and job.standards_snapshot:
        from netbox_compliance.job_results import record_job_results
        record_job_results(job, data.get('summary', {}), now)
    if outcome in ('failed', 'recovery_required'):
        hold_related(job)
    return job


@transaction.atomic
def cancel(user, pk, reason='', recovered=False):
    job = UpgradeJob.objects.restrict(user, 'change').select_for_update().get(pk=pk)
    if recovered:
        if not job.needs_recovery or not reason.strip():
            raise QueueError('Only recovery-required jobs can be released; describe the verified device state.')
    elif job.status not in ('pending', 'held'):
        raise QueueError('Only a pending or held job can be cancelled. An active upgrade must finish recovery.')
    job.status, job.completed_at, job.claim_token = 'cancelled', timezone.now(), None
    job.message = reason or 'Cancelled before dispatch.'
    job.save()
    return job


@transaction.atomic
def hold(user, pk, reason):
    job = UpgradeJob.objects.restrict(user, 'change').select_for_update().get(pk=pk)
    if job.status != 'pending':
        raise QueueError('Only a pending job can be held.')
    if not reason.strip():
        raise QueueError('Give a reason for the hold.')
    job.status, job.held_reason, job.message = 'held', reason.strip()[:1000], f'Held: {reason.strip()}'[:1000]
    job.save()
    return job


def _release(job, reason):
    """Return a held job to the schedule, remembering which failures the person accepted."""
    blamed = set(related_jobs(job).filter(status__in=('failed', 'recovery_required')).values_list('pk', flat=True))
    blamed |= set(UpgradeJob.objects.filter(batch_id=job.batch_id, status__in=('failed', 'recovery_required'),
                                            device__site_id=job.device.site_id).exclude(pk=job.pk).values_list('pk', flat=True))
    job.acknowledged = sorted(set(job.acknowledged or []) | blamed)
    job.message = f'Released: {reason.strip()} (was held: {job.held_reason})'[:1000]
    job.status, job.held_reason = 'pending', ''
    job.save()


@transaction.atomic
def release(user, pk, reason):
    job = UpgradeJob.objects.restrict(user, 'change').select_for_update().get(pk=pk)
    if job.status != 'held':
        raise QueueError('Only a held job can be released.')
    if not reason.strip():
        raise QueueError('Describe why it is safe to continue before releasing the hold.')
    _release(job, reason)
    return job


@transaction.atomic
def release_batch(user, batch_id, reason):
    if not reason.strip():
        raise QueueError('Describe why it is safe to continue before releasing the batch.')
    released = 0
    for job in UpgradeJob.objects.restrict(user, 'change').select_for_update().filter(batch_id=batch_id, status='held'):
        _release(job, reason)
        released += 1
    return released
