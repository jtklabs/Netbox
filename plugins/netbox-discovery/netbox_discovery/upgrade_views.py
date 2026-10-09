"""NetBox schedule form, queue table and per-device progress."""
from django import forms
from django.conf import settings
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.core.exceptions import PermissionDenied, ValidationError
from dcim.models import Device, DeviceType, Site, DeviceRole, Platform, Region
from tenancy.models import Tenant
from extras.models import Tag
import django_tables2 as tables
from netbox.tables import NetBoxTable, columns
from netbox.forms import NetBoxModelFilterSetForm
from django.db.models import Count, Q
from netbox.views.generic import ObjectDeleteView, ObjectEditView, ObjectListView, ObjectView
from netbox.object_actions import AddObject, DeleteObject, EditObject, ObjectAction
from netbox.forms import NetBoxModelForm
from utilities.forms.rendering import FieldSet
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField
from utilities.views import register_model_view

from .models import JobProfile, PrestagePolicy, UpgradeDependency, UpgradeGroup, UpgradeJob, DiscoveryPoller
from .profile_views import RemediationWidget
from . import upgrade_groups, upgrade_prestage
from .upgrade_choices import (CONFIG_OPERATIONS, REMEDIATION_FEATURES, REMEDIATION_MODES, TERMINAL, UpgradeOperationChoices,
                              UpgradeStatusChoices)
from .upgrade_filtersets import (PrestagePolicyFilterSet, UpgradeDependencyFilterSet, UpgradeGroupFilterSet,
                                 UpgradeJobFilterSet)
from . import upgrade_queue as queue


class LocalScheduleDateTimeField(forms.DateTimeField):
    """Defer timezone conversion until the form's selected zone is available."""
    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, datetime):
            return value
        try:
            parsed = parse_datetime(value.strip())
        except (AttributeError, TypeError, ValueError):
            parsed = None
        if parsed is None:
            raise forms.ValidationError(self.error_messages['invalid'], code='invalid')
        return parsed


class UpgradePlanForm(forms.Form):
    operation = forms.ChoiceField(choices=UpgradeOperationChoices, initial='audit')
    time_zone = forms.ChoiceField(choices=[(zone, zone) for zone in sorted(available_timezones())], required=False)
    scheduled_at = LocalScheduleDateTimeField(label='Scheduled start', widget=forms.DateTimeInput(
        format='%Y-%m-%dT%H:%M:%S', attrs={'type': 'datetime-local', 'step': '1'}))
    start_before = LocalScheduleDateTimeField(widget=forms.DateTimeInput(
        format='%Y-%m-%dT%H:%M:%S', attrs={'type': 'datetime-local', 'step': '1'}),
        help_text='Latest start for device changes. Running jobs continue past this time.')
    profile_source = forms.ChoiceField(
        choices=(('model', 'Device defaults (model, then platform)'), ('saved', 'Saved profile'), ('custom', 'Custom settings')),
        initial='model', required=False)
    saved_profile = DynamicModelChoiceField(queryset=JobProfile.objects.all(), required=False,
                                            help_text='Must match the selected operation.')
    profile = forms.CharField(widget=forms.Textarea(attrs={'rows': 15}), required=False,
                              help_text='Audit, staging and upgrades: paste the validated upgrade profile YAML. '
                                        'A copy is saved with every selected device.')
    features = forms.MultipleChoiceField(
        choices=REMEDIATION_FEATURES, required=False, widget=RemediationWidget,
        label='Standards', help_text='Uses the applicable versioned YAML standards stored in NetBox.')
    remediation_mode = forms.ChoiceField(
        choices=REMEDIATION_MODES, initial='add', required=False, label='Comparison mode',
        help_text='Replace also checks for extra entries. Only remediation jobs apply changes.')
    description = forms.CharField(max_length=200, required=False)
    allow_clearpass_cluster_changes = forms.BooleanField(
        label='Allow ClearPass cluster-wide changes', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault('time_zone', settings.TIME_ZONE)
        zone = ZoneInfo(self.initial['time_zone'])
        for field in ('scheduled_at', 'start_before'):
            value = self.initial.get(field)
            if isinstance(value, str):
                value = parse_datetime(value)
            if isinstance(value, datetime):
                self.initial[field] = value.astimezone(zone).replace(tzinfo=None) if timezone.is_aware(value) else value

    def clean_profile_source(self):
        # Older callers post the inline plan without a source selector.
        return self.cleaned_data.get('profile_source') or 'custom'

    def clean_profile(self):
        import yaml
        if self.cleaned_data.get('profile_source') != 'custom' or self.cleaned_data.get('operation') in CONFIG_OPERATIONS:
            return None
        try:
            data = yaml.safe_load(self.cleaned_data['profile'])
            queue.validate_profile(data, self.cleaned_data.get('operation'))
            return data
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean(self):
        data = super().clean()
        zone = ZoneInfo(data.get('time_zone') or settings.TIME_ZONE)
        for field in ('scheduled_at', 'start_before'):
            value = data.get(field)
            if value is None or timezone.is_aware(value):
                continue
            aware = value.replace(tzinfo=zone)
            if aware.astimezone(ZoneInfo('UTC')).astimezone(zone).replace(tzinfo=None) != value:
                self.add_error(field, 'This local time does not exist because of daylight saving time. Choose another time.')
            elif aware.utcoffset() != value.replace(tzinfo=zone, fold=1).utcoffset():
                self.add_error(field, 'This local time occurs twice because of daylight saving time. Select UTC to specify the intended time.')
            else:
                data[field] = aware
        if data.get('profile_source') == 'saved':
            profile = data.get('saved_profile')
            kind = 'remediate' if data.get('operation') in CONFIG_OPERATIONS else 'upgrade'
            if not profile or profile.kind != kind:
                self.add_error('saved_profile', 'Select a saved profile matching this operation.')
        if data.get('profile_source') == 'custom' and data.get('operation') in CONFIG_OPERATIONS:
            # Keep the checkbox order, so the same choice always makes the same job.
            chosen = [value for value, _ in REMEDIATION_FEATURES if value in (data.get('features') or [])]
            data['profile'] = {'features': chosen, 'mode': data.get('remediation_mode') or 'add'}
            if data.get('operation') == 'remediate' and data.get('allow_clearpass_cluster_changes'):
                data['profile']['allow_clearpass_cluster_changes'] = True
            try:
                queue.validate_profile(data['profile'], 'remediate')
            except queue.QueueError as exc:
                self.add_error('features', str(exc))
        return data


def plan_initial(job):
    """A job's plan as form fields: YAML for an upgrade profile, checkboxes for a remediation."""
    import yaml
    if job.operation in CONFIG_OPERATIONS:
        return {'profile_source': 'custom', 'features': job.profile.get('features', []),
                'remediation_mode': job.profile.get('mode', 'add'),
                'allow_clearpass_cluster_changes': job.profile.get('allow_clearpass_cluster_changes', False)}
    return {'profile_source': 'custom', 'profile': yaml.safe_dump(job.profile, sort_keys=False)}


class ScheduleForm(UpgradePlanForm):
    saved_profile = DynamicModelChoiceField(queryset=JobProfile.objects.filter(kind='upgrade'), required=False,
                                            query_params={'kind': 'upgrade'})
    all_active = forms.BooleanField(required=False, label='All active devices')
    regions = DynamicModelMultipleChoiceField(queryset=Region.objects.all(), required=False)
    tenants = DynamicModelMultipleChoiceField(queryset=Tenant.objects.all(), required=False)
    sites = DynamicModelMultipleChoiceField(queryset=Site.objects.all(), required=False)
    roles = DynamicModelMultipleChoiceField(queryset=DeviceRole.objects.all(), required=False)
    platforms = DynamicModelMultipleChoiceField(queryset=Platform.objects.all(), required=False)
    models = DynamicModelMultipleChoiceField(queryset=DeviceType.objects.all(), required=False)
    device_tags = DynamicModelMultipleChoiceField(queryset=Tag.objects.all(), required=False)
    devices = DynamicModelMultipleChoiceField(
        queryset=Device.objects.filter(Q(primary_ip4__isnull=False) | Q(primary_ip6__isnull=False)),
        query_params={'has_primary_ip': True}, required=False,
        help_text='Optional explicit devices; combined with the filters above.')
    poller = DynamicModelChoiceField(queryset=DiscoveryPoller.objects.all(), required=False,
                                    help_text='Normally automatic; choose one when devices have multiple poller tags.')
    field_order = ('all_active', 'regions', 'tenants', 'sites', 'roles', 'platforms', 'models', 'device_tags',
                   'devices', 'poller', 'operation', 'time_zone', 'scheduled_at', 'start_before', 'profile_source', 'saved_profile', 'profile',
                   'features', 'remediation_mode', 'allow_clearpass_cluster_changes', 'description')

    scope_fields = {'regions': 'region_id', 'tenants': 'tenant_id', 'sites': 'site_id', 'roles': 'role_id',
                    'platforms': 'platform_id', 'models': 'device_type_id', 'device_tags': 'tag_id', 'devices': 'id'}

    def __init__(self, *args, **kwargs):
        data = kwargs.get('data', args[0] if args else None)
        if data is not None:
            data = data.copy()
            for old, new in (('site', 'sites'), ('role', 'roles'), ('platform', 'platforms'), ('device_type', 'models')):
                if data.get(old) and new not in data:
                    if hasattr(data, 'setlist'):
                        data.setlist(new, [data[old]])
                    else:
                        data[new] = [data[old]]
            if args:
                args = (data, *args[1:])
            else:
                kwargs['data'] = data
        super().__init__(*args, **kwargs)
        self.fields['operation'].choices = [(value, label) for value, label in UpgradeOperationChoices
                                            if value not in CONFIG_OPERATIONS]
        self.fields['profile_source'].choices = (
            ('model', 'Model defaults'), ('saved', 'Saved profile'), ('custom', 'Custom settings'))
        for name in ('features', 'remediation_mode', 'allow_clearpass_cluster_changes'):
            self.fields.pop(name)

    def clean(self):
        data = super().clean()
        narrowed = any(data.get(field) for field in self.scope_fields)
        if not narrowed and not data.get('all_active'):
            self.add_error('all_active', 'Choose a device scope or explicitly select all active devices.')
        if narrowed and data.get('all_active'):
            self.add_error('all_active', 'Clear All active devices to use a narrower scope.')
        return data

    def schedule_data(self):
        data = dict(self.cleaned_data)
        data['filters'] = {'status': ['active'], **{
            key: [obj.pk for obj in data[field]] for field, key in self.scope_fields.items() if data.get(field)}}
        data['poller'] = data['poller'].name if data['poller'] else ''
        return data


class UpgradeJobEditForm(UpgradePlanForm):
    last_updated = forms.DateTimeField(widget=forms.HiddenInput)

    def __init__(self, *args, job=None, **kwargs):
        super().__init__(*args, **kwargs)
        if job is not None:
            is_config = job.operation in CONFIG_OPERATIONS
            self.fields['operation'].choices = [(value, label) for value, label in UpgradeOperationChoices
                if (value in CONFIG_OPERATIONS) == is_config and (not job.audit_run_id or value == job.operation)]
            self.fields['saved_profile'].queryset = JobProfile.objects.filter(kind='remediate' if is_config else 'upgrade')
            if not is_config:
                for name in ('features', 'remediation_mode', 'allow_clearpass_cluster_changes'):
                    self.fields.pop(name)


class UpgradeJobEditView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.change_upgradejob'
    raise_exception = True

    def get_job(self, request, pk):
        return get_object_or_404(UpgradeJob.objects.restrict(request.user, 'change'), pk=pk)

    def get(self, request, pk):
        job = self.get_job(request, pk)
        if job.status != 'pending':
            messages.error(request, 'Only pending jobs can be edited. This job has already been claimed or closed.')
            return redirect(job.get_absolute_url())
        form = UpgradeJobEditForm(job=job, initial={
            'operation': job.operation, 'scheduled_at': job.scheduled_at.isoformat(),
            'start_before': job.start_before.isoformat(), 'description': job.description,
            'last_updated': job.last_updated.isoformat(), **plan_initial(job),
        })
        return render(request, 'netbox_discovery/upgradejob_edit.html', {'object': job, 'form': form})

    def post(self, request, pk):
        job = self.get_job(request, pk)
        form = UpgradeJobEditForm(request.POST, job=job)
        if form.is_valid():
            try:
                job = queue.edit_pending(request.user, pk, form.cleaned_data)
                messages.success(request, 'Device job updated.')
                return redirect(job.get_absolute_url())
            except (queue.QueueError, ValidationError) as exc:
                form.add_error(None, str(exc))
        return render(request, 'netbox_discovery/upgradejob_edit.html', {'object': job, 'form': form})


class UpgradeScheduleView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.add_upgradejob'
    raise_exception = True

    def get(self, request):
        source = request.GET.get('from_job', '')
        config_job = source.isdigit() and UpgradeJob.objects.restrict(request.user, 'view').filter(
            pk=int(source), operation__in=CONFIG_OPERATIONS).exists()
        if request.GET.get('operation') in CONFIG_OPERATIONS or config_job:
            params = request.GET.copy()
            params['frequency'] = 'now'
            return redirect(reverse('plugins:netbox_discovery:auditschedule_add') + '?' + params.urlencode())
        return render(request, 'netbox_discovery/upgrade_schedule.html', {'form': ScheduleForm(initial=self.initial(request))})

    @staticmethod
    def initial(request):
        """A closed job's device, plan and profile, when the form is opened from one."""
        from django.utils import timezone
        source = request.GET.get('from_job', '')
        job = UpgradeJob.objects.restrict(request.user, 'view').filter(pk=int(source)).first() if source.isdigit() else None
        if job is None:
            # Opened with a device selection, e.g. from the Device Compliance grid.
            initial = {}
            model = request.GET.get('device_type', '')
            if model.isdigit():
                initial['models'] = [int(model)]
            devices = [int(pk) for pk in request.GET.getlist('devices') if pk.isdigit()]
            if devices:
                initial['devices'] = devices
            if request.GET.get('operation') in UpgradeOperationChoices.values():
                initial['operation'] = request.GET['operation']
            features = [value for value, _ in REMEDIATION_FEATURES if value in request.GET.getlist('features')]
            if features:
                initial['features'] = features
                initial['profile_source'] = 'custom'
            return initial
        scheduled_at, start_before = queue.requeue_window(job, timezone.now())
        return {'devices': [job.device_id], 'poller': job.poller_id, 'operation': job.operation,
                'scheduled_at': scheduled_at.isoformat(timespec='minutes'), 'start_before': start_before.isoformat(timespec='minutes'),
                'description': job.description, **plan_initial(job)}

    def post(self, request):
        form, rows = ScheduleForm(request.POST), []
        if form.is_valid():
            try:
                data = form.schedule_data()
                if request.POST.get('schedule') == 'yes':
                    jobs = queue.schedule(request.user, data)
                    messages.success(request, f'Scheduled {len(jobs)} device(s).')
                    return redirect(reverse('plugins:netbox_discovery:upgradejob_list') + '?batch_id=' + str(jobs[0].batch_id))
                rows = queue.prepare(request.user, data)
            except (queue.QueueError, ValidationError) as exc:
                form.add_error(None, str(exc))
        return render(request, 'netbox_discovery/upgrade_schedule.html', {'form': form, 'preview': rows})


class UpgradeJobTable(NetBoxTable):
    actions = columns.ActionsColumn(actions=('changelog',))
    device_name = tables.Column(linkify=lambda record: record.get_absolute_url())
    poller = tables.Column(linkify=True)
    status = columns.ChoiceFieldColumn()
    operation = columns.ChoiceFieldColumn()
    scheduled_at = columns.DateTimeColumn(verbose_name='Scheduled start')
    last_seen_at = columns.DateTimeColumn(verbose_name='Job last update', default='No job updates yet')
    groups = columns.TemplateColumn(template_code='{{ value|join:", " }}', verbose_name='Groups', orderable=False)
    planned_wave = tables.Column(verbose_name='Wave')
    poller_last_seen_at = columns.DateTimeColumn(accessor='poller__upgrade_last_seen_at',
                                                verbose_name='Worker last seen', default='Never checked in')

    class Meta(NetBoxTable.Meta):
        model = UpgradeJob
        fields = ('pk', 'id', 'device_name', 'poller', 'operation', 'status', 'scheduled_at', 'planned_wave', 'groups',
                  'held_reason', 'start_before', 'stage', 'message', 'poller_last_seen_at', 'last_seen_at', 'description', 'batch_id')
        default_columns = ('device_name', 'poller', 'operation', 'scheduled_at', 'planned_wave', 'status', 'stage',
                           'poller_last_seen_at', 'last_seen_at')


class UpgradeFilterForm(NetBoxModelFilterSetForm):
    model = UpgradeJob
    status = forms.ChoiceField(choices=[('', '---------')] + list(UpgradeStatusChoices), required=False)
    operation = forms.ChoiceField(choices=[('', '---------')] + [
        (value, label) for value, label in UpgradeOperationChoices if value not in CONFIG_OPERATIONS], required=False)
    poller_id = DynamicModelChoiceField(queryset=DiscoveryPoller.objects.all(), required=False)
    site_id = DynamicModelChoiceField(queryset=Site.objects.all(), required=False)
    role_id = DynamicModelChoiceField(queryset=DeviceRole.objects.all(), required=False)


class StandardsJobFilterForm(UpgradeFilterForm):
    operation = forms.ChoiceField(choices=[('', '---------')] + [
        (value, label) for value, label in UpgradeOperationChoices if value in CONFIG_OPERATIONS], required=False)


class BulkHoldJobs(ObjectAction):
    name = 'bulk_hold'
    label = 'Hold selected'
    multi = True
    permissions_required = {'change'}
    template_name = 'netbox_discovery/buttons/bulk_jobs.html'


class BulkCancelJobs(BulkHoldJobs):
    name = 'bulk_cancel'
    label = 'Cancel selected'


class BulkDeleteJobs(BulkHoldJobs):
    name = 'bulk_delete'
    label = 'Delete selected'
    permissions_required = {'delete'}


@register_model_view(UpgradeJob, name='list')
class UpgradeJobListView(ObjectListView):
    queryset = UpgradeJob.objects.exclude(operation__in=CONFIG_OPERATIONS).select_related('device', 'poller')
    template_name = 'netbox_discovery/upgradejob_list.html'
    table = UpgradeJobTable
    filterset = UpgradeJobFilterSet
    filterset_form = UpgradeFilterForm
    actions = (AddObject, BulkHoldJobs, BulkCancelJobs, BulkDeleteJobs)


class StandardsJobListView(UpgradeJobListView):
    queryset = UpgradeJob.objects.filter(operation__in=CONFIG_OPERATIONS).select_related('device', 'poller')
    template_name = 'netbox_discovery/standardsjob_list.html'
    filterset_form = StandardsJobFilterForm
    actions = (BulkHoldJobs, BulkCancelJobs, BulkDeleteJobs)


class BulkJobForm(forms.Form):
    selection = forms.JSONField(widget=forms.HiddenInput)
    reason = forms.CharField(max_length=1000, required=False,
                            widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}))

    def clean_selection(self):
        values = self.cleaned_data['selection']
        if not isinstance(values, list) or not values or any(type(pk) is not int or pk < 1 for pk in values):
            raise forms.ValidationError('Select at least one job.')
        return sorted(set(values))


class UpgradeBulkActionView(PermissionRequiredMixin, View):
    action = None
    raise_exception = True

    def get_permission_required(self):
        permission = 'delete' if self.action == 'delete' else 'change'
        return (f'netbox_discovery.{permission}_upgradejob',)

    def post(self, request):
        queryset = UpgradeJob.objects.restrict(request.user, 'view')
        scope = request.GET.get('job_scope')
        list_url = 'plugins:netbox_discovery:standardsjob_list' if scope == 'standards' else 'plugins:netbox_discovery:upgradejob_list'
        if scope == 'standards':
            queryset = queryset.filter(operation__in=CONFIG_OPERATIONS)
        elif scope == 'upgrade':
            queryset = queryset.exclude(operation__in=CONFIG_OPERATIONS)
        form = BulkJobForm(request.POST) if request.POST.get('confirm') == 'yes' else None
        if form is None:
            if request.POST.get('_all'):
                filtered = UpgradeJobFilterSet(request.GET, queryset=queryset, request=request)
                if not filtered.is_valid():
                    messages.error(request, 'Invalid filters. No jobs were changed.')
                    return redirect(list_url)
                pks = list(filtered.qs.values_list('pk', flat=True))
            else:
                try:
                    pks = [int(pk) for pk in request.POST.getlist('pk')]
                except ValueError:
                    pks = []
            if not pks:
                messages.warning(request, 'Select at least one job.')
                return redirect(list_url)
            form = BulkJobForm(initial={'selection': pks})
        elif form.is_valid():
            pks = form.cleaned_data['selection']
            try:
                if queryset.filter(pk__in=pks).count() != len(pks):
                    raise queue.QueueError('Select accessible jobs in this section only.')
                count = queue.bulk_action(request.user, pks, self.action, form.cleaned_data['reason'])
                messages.success(request, f'{count} jobs ' + {'hold': 'held.', 'cancel': 'cancelled.', 'delete': 'deleted.'}[self.action])
                return redirect(list_url)
            except queue.QueueError as exc:
                form.add_error(None, str(exc))
        else:
            pks = []
        return render(request, 'netbox_discovery/upgradejob_bulk.html', {
            'form': form, 'jobs': queryset.filter(pk__in=pks).order_by('pk'),
            'action': self.action, 'title': f'{self.action.title()} scheduled jobs', 'list_url': list_url,
        })


@register_model_view(UpgradeJob)
class UpgradeJobView(ObjectView):
    queryset = UpgradeJob.objects.select_related('device', 'poller', 'requested_by')
    actions = (EditObject,)

    def get_permitted_actions(self, user, model=None):
        if model.status != 'pending' or not UpgradeJob.objects.restrict(user, 'change').filter(pk=model.pk).exists():
            return ()
        return super().get_permitted_actions(user, model)

    def get_extra_context(self, request, instance):
        import json
        return {'profile_text': json.dumps(instance.profile, indent=2),
                'summary_text': json.dumps(instance.summary, indent=2),
                'requeueable': instance.status in TERMINAL,
                'waits_for_devices': Device.objects.filter(pk__in=instance.waits_for),
                'held_in_batch': UpgradeJob.objects.filter(batch_id=instance.batch_id, status='held').count()}


class UpgradeCancelView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.change_upgradejob'
    raise_exception = True

    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        entry = get_object_or_404(UpgradeJob.objects.restrict(request.user, 'change'), pk=pk)
        try:
            queue.cancel(request.user, pk, request.POST.get('reason', '')[:1000],
                         recovered=request.POST.get('recovered') == 'yes')
            messages.success(request, 'Job closed.')
        except queue.QueueError as exc:
            messages.error(request, str(exc))
        return redirect(entry.get_absolute_url())


class UpgradeRequeueView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.add_upgradejob'
    raise_exception = True

    def post(self, request, pk):
        entry = get_object_or_404(UpgradeJob.objects.restrict(request.user, 'view'), pk=pk)
        try:
            job = queue.requeue(request.user, pk)
            messages.success(request, 'Re-queued as a new job, due now.')
            return redirect(job.get_absolute_url())
        except (queue.QueueError, ValidationError) as exc:
            messages.error(request, str(exc))
        return redirect(entry.get_absolute_url())


class UpgradeHoldView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.change_upgradejob'
    raise_exception = True

    def post(self, request, pk):
        entry = get_object_or_404(UpgradeJob.objects.restrict(request.user, 'change'), pk=pk)
        try:
            queue.hold(request.user, pk, request.POST.get('reason', ''))
            messages.success(request, 'Job held.')
        except queue.QueueError as exc:
            messages.error(request, str(exc))
        return redirect(entry.get_absolute_url())


class UpgradeReleaseView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.change_upgradejob'
    raise_exception = True

    def post(self, request, pk):
        entry = get_object_or_404(UpgradeJob.objects.restrict(request.user, 'change'), pk=pk)
        try:
            if request.POST.get('scope') == 'batch':
                count = queue.release_batch(request.user, entry.batch_id, request.POST.get('reason', ''))
                messages.success(request, f'Released {count} held job(s) in this batch.')
            else:
                queue.release(request.user, pk, request.POST.get('reason', ''))
                messages.success(request, 'Hold released; the job is scheduled again.')
        except queue.QueueError as exc:
            messages.error(request, str(exc))
        return redirect(entry.get_absolute_url())


class UpgradeGroupForm(NetBoxModelForm):
    members = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False)
    depends_on = DynamicModelMultipleChoiceField(queryset=UpgradeGroup.objects.all(), required=False, label='Waits for groups',
                                                 help_text='Their members go first, and the groups never upgrade at the same time')
    fieldsets = (FieldSet('name', 'max_concurrent', 'members', 'depends_on', 'description', name='Redundancy group'),
                 FieldSet('tags', name='Tags'))

    class Meta:
        model = UpgradeGroup
        fields = ('name', 'max_concurrent', 'members', 'depends_on', 'description', 'comments', 'tags')
        help_texts = {'max_concurrent': 'A pair is 1; 0 lets every member go at once, for example all access switches in an office.'}

    def clean_depends_on(self):
        groups = self.cleaned_data['depends_on']
        if self.instance.pk and self.instance in groups:
            raise forms.ValidationError('A group cannot wait for itself.')
        return groups


class UpgradeGroupTable(NetBoxTable):
    name = tables.Column(linkify=True)
    source = columns.ChoiceFieldColumn()
    stale = columns.BooleanColumn()
    member_count = tables.Column(accessor='members__count', verbose_name='Members', orderable=False)
    depends_on = columns.ManyToManyColumn(linkify_item=True, verbose_name='Waits for')

    class Meta(NetBoxTable.Meta):
        model = UpgradeGroup
        fields = ('pk', 'id', 'name', 'max_concurrent', 'source', 'stale', 'member_count', 'depends_on', 'description')
        default_columns = ('name', 'max_concurrent', 'source', 'stale', 'member_count', 'depends_on')


@register_model_view(UpgradeGroup, name='list')
class UpgradeGroupListView(ObjectListView):
    queryset = UpgradeGroup.objects.annotate(Count('members'))
    table = UpgradeGroupTable
    filterset = UpgradeGroupFilterSet
    actions = (AddObject,)
    template_name = 'netbox_discovery/upgradegroup_list.html'


@register_model_view(UpgradeGroup)
class UpgradeGroupView(ObjectView):
    queryset = UpgradeGroup.objects.prefetch_related('members__site', 'members__role', 'depends_on', 'dependents')
    actions = (EditObject, DeleteObject)


@register_model_view(UpgradeGroup, 'edit')
class UpgradeGroupEditView(ObjectEditView):
    queryset = UpgradeGroup.objects.all()
    form = UpgradeGroupForm


@register_model_view(UpgradeGroup, 'delete')
class UpgradeGroupDeleteView(ObjectDeleteView):
    queryset = UpgradeGroup.objects.all()


class UpgradeGroupRefreshView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.change_upgradegroup'
    raise_exception = True

    def post(self, request):
        counts = upgrade_groups.refresh_discovered()
        messages.success(request, f"Discovered groups refreshed: {counts['groups']} FHRP group(s), "
                                  f"{counts['dependencies']} cabled dependency(ies); "
                                  f"{counts['stale_groups'] + counts['stale_dependencies']} marked stale.")
        return redirect(reverse('plugins:netbox_discovery:upgradegroup_list'))


class UpgradeDependencyForm(NetBoxModelForm):
    upstream = DynamicModelChoiceField(queryset=Device.objects.all(), help_text='Waits: upgraded only after the downstream device')
    downstream = DynamicModelChoiceField(queryset=Device.objects.all(), help_text='Goes first')
    fieldsets = (FieldSet('upstream', 'downstream', 'description', name='Dependency'), FieldSet('tags', name='Tags'))

    class Meta:
        model = UpgradeDependency
        fields = ('upstream', 'downstream', 'description', 'comments', 'tags')


class UpgradeDependencyTable(NetBoxTable):
    upstream = tables.Column(linkify=True)
    downstream = tables.Column(linkify=True)
    source = columns.ChoiceFieldColumn()
    stale = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = UpgradeDependency
        fields = ('pk', 'id', 'upstream', 'downstream', 'source', 'stale', 'description')
        default_columns = ('upstream', 'downstream', 'source', 'stale')


@register_model_view(UpgradeDependency, name='list')
class UpgradeDependencyListView(ObjectListView):
    queryset = UpgradeDependency.objects.select_related('upstream', 'downstream')
    table = UpgradeDependencyTable
    filterset = UpgradeDependencyFilterSet
    actions = (AddObject,)


@register_model_view(UpgradeDependency)
class UpgradeDependencyView(ObjectView):
    queryset = UpgradeDependency.objects.select_related('upstream', 'downstream')
    actions = (EditObject, DeleteObject)


@register_model_view(UpgradeDependency, 'edit')
class UpgradeDependencyEditView(ObjectEditView):
    queryset = UpgradeDependency.objects.all()
    form = UpgradeDependencyForm


@register_model_view(UpgradeDependency, 'delete')
class UpgradeDependencyDeleteView(ObjectDeleteView):
    queryset = UpgradeDependency.objects.all()


class PrestagePolicyForm(NetBoxModelForm):
    device_type = DynamicModelChoiceField(queryset=DeviceType.objects.all(), label='Model',
                                          help_text='Every active device of this model, one job per stack master')
    fieldsets = (FieldSet('device_type', 'enabled', 'interval_hours', 'window_hours', 'minimum_free_bytes', 'description',
                          name='Image staging policy'),
                 FieldSet('tags', name='Tags'))

    class Meta:
        model = PrestagePolicy
        fields = ('device_type', 'enabled', 'interval_hours', 'window_hours', 'minimum_free_bytes', 'description',
                  'comments', 'tags')


class PrestagePolicyTable(NetBoxTable):
    device_type = tables.Column(linkify=True, verbose_name='Model')
    enabled = columns.BooleanColumn()
    last_run_at = columns.DateTimeColumn(verbose_name='Last run')
    last_scheduled = tables.Column(accessor='last_summary__scheduled', verbose_name='Last scheduled', orderable=False)

    class Meta(NetBoxTable.Meta):
        model = PrestagePolicy
        fields = ('pk', 'id', 'device_type', 'enabled', 'interval_hours', 'window_hours', 'minimum_free_bytes',
                  'last_run_at', 'last_scheduled', 'description')
        default_columns = ('device_type', 'enabled', 'interval_hours', 'window_hours', 'last_run_at', 'last_scheduled')


class ApplyRequiredMixin:
    """Enabling or changing a policy schedules image copies, so it needs apply permission."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm('netbox_discovery.apply_upgradejob'):
            raise PermissionDenied('Image staging policies need apply permission on upgrade jobs.')
        return super().dispatch(request, *args, **kwargs)


@register_model_view(PrestagePolicy, name='list')
class PrestagePolicyListView(ObjectListView):
    queryset = PrestagePolicy.objects.select_related('device_type__manufacturer')
    table = PrestagePolicyTable
    filterset = PrestagePolicyFilterSet
    actions = (AddObject,)
    template_name = 'netbox_discovery/prestagepolicy_list.html'


@register_model_view(PrestagePolicy)
class PrestagePolicyView(ObjectView):
    queryset = PrestagePolicy.objects.select_related('device_type__manufacturer')
    actions = (EditObject, DeleteObject)

    def get_extra_context(self, request, instance):
        try:
            rows, error = upgrade_prestage.plan(instance), ''
        except queue.QueueError as exc:
            rows, error = [], str(exc)
        return {'rows': rows, 'plan_error': error, 'summary': upgrade_prestage.summarize(rows),
                'jobs_url': reverse('plugins:netbox_discovery:upgradejob_list')
                + f'?operation=stage&device_type_id={instance.device_type_id}'}


@register_model_view(PrestagePolicy, 'edit')
class PrestagePolicyEditView(ApplyRequiredMixin, ObjectEditView):
    queryset = PrestagePolicy.objects.all()
    form = PrestagePolicyForm


@register_model_view(PrestagePolicy, 'delete')
class PrestagePolicyDeleteView(ObjectDeleteView):
    queryset = PrestagePolicy.objects.all()


class PrestageRunView(PermissionRequiredMixin, View):
    """Run now instead of waiting for the next scheduled run, e.g. after changing a standard."""
    permission_required = 'netbox_discovery.apply_upgradejob'
    raise_exception = True

    def post(self, request, pk=None):
        if pk is not None:
            policy = get_object_or_404(PrestagePolicy.objects.restrict(request.user, 'view'), pk=pk)
            try:
                jobs, summary = upgrade_prestage.run_policy(policy)
            except queue.QueueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Scheduled {summary['scheduled']} staging job(s); "
                                          f"{summary['current']} already current, {summary['skipped']} skipped.")
            return redirect(policy.get_absolute_url())
        results = upgrade_prestage.run()
        scheduled = sum(summary.get('scheduled', 0) for summary in results.values())
        failed = [name for name, summary in results.items() if 'error' in summary]
        messages.success(request, f'Ran {len(results)} enabled policy(ies); scheduled {scheduled} staging job(s).')
        if failed:
            messages.error(request, f'Could not run: {", ".join(failed)}. See each policy for the error.')
        return redirect(reverse('plugins:netbox_discovery:prestagepolicy_list'))
