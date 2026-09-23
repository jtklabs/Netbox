"""NetBox schedule form, queue table and per-device progress."""
from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.core.exceptions import PermissionDenied, ValidationError
from dcim.models import Device, DeviceType, Site, DeviceRole, Platform
import django_tables2 as tables
from netbox.tables import NetBoxTable, columns
from netbox.forms import NetBoxModelFilterSetForm
from django.db.models import Count
from netbox.views.generic import ObjectDeleteView, ObjectEditView, ObjectListView, ObjectView
from netbox.object_actions import AddObject, DeleteObject, EditObject
from netbox.forms import NetBoxModelForm
from utilities.forms.rendering import FieldSet
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField
from utilities.views import register_model_view

from .models import PrestagePolicy, UpgradeDependency, UpgradeGroup, UpgradeJob, DiscoveryPoller
from . import upgrade_groups, upgrade_prestage
from .upgrade_choices import (REMEDIATION_FEATURES, REMEDIATION_MODES, TERMINAL, UpgradeOperationChoices,
                              UpgradeStatusChoices)
from .upgrade_filtersets import (PrestagePolicyFilterSet, UpgradeDependencyFilterSet, UpgradeGroupFilterSet,
                                 UpgradeJobFilterSet)
from . import upgrade_queue as queue


class UpgradePlanForm(forms.Form):
    operation = forms.ChoiceField(choices=UpgradeOperationChoices, initial='audit')
    scheduled_at = forms.DateTimeField(help_text='Include a UTC offset, for example 2026-09-20T22:00:00-04:00.')
    start_before = forms.DateTimeField(help_text='Latest start for device changes. Running jobs continue past this time.')
    profile = forms.CharField(widget=forms.Textarea(attrs={'rows': 15}), required=False,
                              help_text='Audit, staging and upgrades: paste the validated upgrade profile YAML. '
                                        'A copy is saved with every selected device.')
    features = forms.MultipleChoiceField(
        choices=REMEDIATION_FEATURES, required=False, widget=forms.CheckboxSelectMultiple,
        label='Remediate', help_text="Remediation only. Each poller applies its own standards.yaml for these.")
    remediation_mode = forms.ChoiceField(
        choices=REMEDIATION_MODES, initial='add', required=False, label='Remediation mode',
        help_text='Replace removes entries the standard does not list, such as an old NTP server.')
    description = forms.CharField(max_length=200, required=False)

    def clean_profile(self):
        import yaml
        if self.cleaned_data.get('operation') == 'remediate':
            return None
        try:
            data = yaml.safe_load(self.cleaned_data['profile'])
            queue.validate_profile(data, self.cleaned_data.get('operation'))
            return data
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean(self):
        data = super().clean()
        if data.get('operation') == 'remediate':
            # Keep the checkbox order, so the same choice always makes the same job.
            chosen = [value for value, _ in REMEDIATION_FEATURES if value in (data.get('features') or [])]
            data['profile'] = {'features': chosen, 'mode': data.get('remediation_mode') or 'add'}
            try:
                queue.validate_profile(data['profile'], 'remediate')
            except queue.QueueError as exc:
                self.add_error('features', str(exc))
        return data


def plan_initial(job):
    """A job's plan as form fields: YAML for an upgrade profile, checkboxes for a remediation."""
    import yaml
    if job.operation == 'remediate':
        return {'features': job.profile.get('features', []), 'remediation_mode': job.profile.get('mode', 'add')}
    return {'profile': yaml.safe_dump(job.profile, sort_keys=False)}


class ScheduleForm(UpgradePlanForm):
    site = DynamicModelChoiceField(queryset=Site.objects.all(), required=False)
    role = DynamicModelChoiceField(queryset=DeviceRole.objects.all(), required=False)
    platform = DynamicModelChoiceField(queryset=Platform.objects.all(), required=False)
    devices = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False,
                                              help_text='Optional explicit devices; combined with the filters above.')
    poller = DynamicModelChoiceField(queryset=DiscoveryPoller.objects.all(), required=False,
                                    help_text='Normally automatic; choose one when devices have multiple poller tags.')
    field_order = ('site', 'role', 'platform', 'devices', 'poller', 'operation',
                   'scheduled_at', 'start_before', 'profile', 'features', 'remediation_mode', 'description')

    def schedule_data(self):
        data = dict(self.cleaned_data)
        data['filters'] = {key + '_id': [data[key].pk] for key in ('site', 'role', 'platform') if data[key]}
        if data['devices']:
            data['filters']['id'] = [obj.pk for obj in data['devices']]
        data['poller'] = data['poller'].name if data['poller'] else ''
        return data


class UpgradeJobEditForm(UpgradePlanForm):
    last_updated = forms.DateTimeField(widget=forms.HiddenInput)


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
        form = UpgradeJobEditForm(initial={
            'operation': job.operation, 'scheduled_at': job.scheduled_at.isoformat(),
            'start_before': job.start_before.isoformat(), 'description': job.description,
            'last_updated': job.last_updated.isoformat(), **plan_initial(job),
        })
        return render(request, 'netbox_discovery/upgradejob_edit.html', {'object': job, 'form': form})

    def post(self, request, pk):
        job = self.get_job(request, pk)
        form = UpgradeJobEditForm(request.POST)
        if form.is_valid():
            try:
                job = queue.edit_pending(request.user, pk, form.cleaned_data)
                messages.success(request, 'Upgrade job updated.')
                return redirect(job.get_absolute_url())
            except (queue.QueueError, ValidationError) as exc:
                form.add_error(None, str(exc))
        return render(request, 'netbox_discovery/upgradejob_edit.html', {'object': job, 'form': form})


class UpgradeScheduleView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.add_upgradejob'
    raise_exception = True

    def get(self, request):
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
            devices = [int(pk) for pk in request.GET.getlist('devices') if pk.isdigit()]
            if devices:
                initial['devices'] = devices
            if request.GET.get('operation') in UpgradeOperationChoices.values():
                initial['operation'] = request.GET['operation']
            features = [value for value, _ in REMEDIATION_FEATURES if value in request.GET.getlist('features')]
            if features:
                initial['features'] = features
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
    scheduled_at = columns.DateTimeColumn()
    last_seen_at = columns.DateTimeColumn(verbose_name='Job last update', default='No job updates yet')
    groups = columns.TemplateColumn(template_code='{{ value|join:", " }}', verbose_name='Groups', orderable=False)
    planned_wave = tables.Column(verbose_name='Wave')
    poller_last_seen_at = columns.DateTimeColumn(accessor='poller__upgrade_last_seen_at',
                                                verbose_name='Upgrade poller last seen', default='Never checked in')

    class Meta(NetBoxTable.Meta):
        model = UpgradeJob
        fields = ('pk', 'id', 'device_name', 'poller', 'operation', 'status', 'scheduled_at', 'planned_wave', 'groups',
                  'held_reason', 'start_before', 'stage', 'message', 'poller_last_seen_at', 'last_seen_at', 'description', 'batch_id')
        default_columns = ('device_name', 'poller', 'operation', 'scheduled_at', 'planned_wave', 'status', 'stage',
                           'poller_last_seen_at', 'last_seen_at')


class UpgradeFilterForm(NetBoxModelFilterSetForm):
    model = UpgradeJob
    status = forms.ChoiceField(choices=[('', '---------')] + list(UpgradeStatusChoices), required=False)
    operation = forms.ChoiceField(choices=[('', '---------')] + list(UpgradeOperationChoices), required=False)
    poller_id = DynamicModelChoiceField(queryset=DiscoveryPoller.objects.all(), required=False)
    site_id = DynamicModelChoiceField(queryset=Site.objects.all(), required=False)
    role_id = DynamicModelChoiceField(queryset=DeviceRole.objects.all(), required=False)


@register_model_view(UpgradeJob, name='list')
class UpgradeJobListView(ObjectListView):
    queryset = UpgradeJob.objects.select_related('device', 'poller')
    table = UpgradeJobTable
    filterset = UpgradeJobFilterSet
    filterset_form = UpgradeFilterForm
    actions = (AddObject,)


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
                          name='Prestage policy'),
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
            raise PermissionDenied('Prestage policies schedule image staging and need apply permission on upgrade jobs.')
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
