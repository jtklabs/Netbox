"""Immediate device standards runs using the existing queue and permissions."""
from datetime import timedelta

from dcim.models import Device
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View

from . import upgrade_queue as queue
from .models import JobProfile, UpgradeJob
from .profile_assignments import default_profile_ids
from .upgrade_choices import ACTIVE, WAITING


class ExistingWork(queue.QueueError):
    def __init__(self, job=None):
        self.job = job
        if job is None:
            message = 'This device has queued or active work that you cannot view. Ask a job operator to review it.'
        else:
            guidance = ('Verify the device and stop any surviving worker, then use Record recovery and release device.'
                        if job.needs_recovery else
                        'Cancel or release this held job before starting another run.' if job.status == 'held' else
                        'Wait for this job to finish, or cancel it if it has not started.')
            message = f'Job #{job.pk} is blocking this run ({job.get_status_display()}). {guidance}'
        super().__init__(message)


def choices_for(user, device):
    from netbox_compliance.definitions import FEATURE_SECTIONS, SnapshotResolver

    defaults = default_profile_ids(user, [device], 'remediate')
    profiles = list(JobProfile.objects.restrict(user, 'view').filter(kind='remediate').order_by('name'))
    assigned = next((p for p in profiles if p.pk == defaults.get(device.pk)), None)
    choices = []
    for standard, revision in SnapshotResolver(user).for_device(device):
        section, settings = next(iter(revision.definition['settings'].items()))
        features = [feature for feature, key in FEATURE_SECTIONS.items() if key == section]
        if section == 'snmp':
            features = (['snmp'] if set(settings) - {'packetsize'} else [])
            if 'packetsize' in settings:
                features.append('snmp_packetsize')
        if not features:
            continue
        # Policy is resolved again on POST. No commands or frozen settings come
        # from the browser, and selecting one standard never applies its peers.
        mode = assigned.plan['mode'] if assigned else 'add'
        if mode == 'replace' and not standard.allow_enforce:
            mode = 'add'
        operation = 'remediate' if standard.auto_remediable else 'audit_config'
        plan = {'features': features, 'mode': mode}
        if section == 'raw_commands':
            plan['raw_standard_id'] = standard.pk
        if assigned and assigned.plan.get('allow_clearpass_cluster_changes') and 'ntp' in features:
            plan['allow_clearpass_cluster_changes'] = True
        choices.append({'value': f'standard:{standard.pk}', 'label': standard.name,
                        'plan': plan, 'operation': operation, 'profile_name': assigned.name if assigned else ''})
    choices.sort(key=lambda item: ('ntp' not in item['plan']['features'], item['label']))
    for profile in profiles:
        choices.append({'value': f'profile:{profile.pk}',
                        'label': f'Profile: {profile.name}' + (' (assigned)' if profile == assigned else ''),
                        'plan': profile.resolved_plan(), 'operation': 'remediate', 'profile_name': profile.name})
    return choices


@transaction.atomic
def run_now(user, device, selection, action):
    from netbox_compliance.definitions import execution_checks, snapshot_for_device

    if not user.has_perm('netbox_discovery.add_upgradejob'):
        raise queue.QueueError('You do not have permission to run device standards.')
    targets = queue.execution_devices(user, Device.objects.restrict(user, 'view').filter(pk=device.pk))
    if not targets:
        raise queue.QueueError('This device is not accessible.')
    target = targets[0]
    Device.objects.select_for_update().get(pk=target.pk)
    if target.status != 'active':
        raise queue.QueueError('The execution device must be active.')
    choice = next((item for item in choices_for(user, target) if item['value'] == selection), None)
    if choice is None:
        raise queue.QueueError('Select an accessible standard or profile that applies to this device.')
    if action not in ('auto', 'audit', 'add', 'replace'):
        raise queue.QueueError('Choose standard settings, audit, add missing, or add and remove.')
    plan = dict(choice['plan'])
    if action in ('add', 'replace'):
        plan['mode'] = action
    # Keep each standard's permissions in the pinned job. A mixed profile can
    # remediate permitted standards while the remaining checks stay read-only.
    snapshot = snapshot_for_device(user, target, plan['features'], plan['mode'], audit=True,
                                   raw_standard_id=plan.get('raw_standard_id'),
                                   raw_standard_ids=plan.get('raw_standard_ids'))
    checks = set(execution_checks(plan['features'], plan.get('raw_standard_ids')))
    operation = ('audit_config' if action == 'audit' or checks.issubset(snapshot['read_only_features'])
                 else 'remediate')
    if operation == 'remediate' and not user.has_perm('netbox_discovery.apply_upgradejob'):
        raise queue.QueueError('You do not have permission to apply changes. Select Audit only.')
    now = timezone.now()
    # An offline poller cannot expire missed start windows. These jobs can no
    # longer start, so they must not prevent a fresh run from the device page.
    UpgradeJob.objects.filter(device=target, status__in=WAITING, start_before__lte=now).update(
        status='expired', completed_at=now, message='Start window missed; no work dispatched.')
    blocking = UpgradeJob.objects.filter(device=target, status__in=(*ACTIVE, *WAITING))
    if blocking.exists():
        raise ExistingWork(blocking.restrict(user, 'view').order_by('scheduled_at', 'pk').first())
    job = queue.schedule(user, {'operation': operation, 'filters': {'id': [target.pk]},
                               'profile_source': 'custom', 'profile': plan,
                               'scheduled_at': now, 'start_before': now + timedelta(hours=1),
                               'description': f'Run now: {choice["label"]}'})[0]
    job.profile_name = choice['profile_name']
    job.save(update_fields=['profile_name'])
    return job


class DeviceRunNowView(PermissionRequiredMixin, View):
    permission_required = ('dcim.view_device', 'netbox_discovery.add_upgradejob')
    raise_exception = True

    def post(self, request, pk):
        device = get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)
        try:
            job = run_now(request.user, device, request.POST.get('selection'), request.POST.get('action', 'auto'))
        except ExistingWork as exc:
            messages.error(request, str(exc))
            return redirect(exc.job.get_absolute_url() if exc.job else device.get_absolute_url())
        except (queue.QueueError, ValidationError) as exc:
            messages.error(request, '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
            return redirect(device.get_absolute_url())
        messages.success(request, f'{job.device_name}: queued to run on the next {job.poller.name} worker check-in.')
        return redirect(job.get_absolute_url())
