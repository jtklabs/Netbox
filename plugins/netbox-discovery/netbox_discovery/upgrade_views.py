"""NetBox schedule form, queue table and per-device progress."""
from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.core.exceptions import ValidationError
from dcim.models import Device, Site, DeviceRole, Platform
import django_tables2 as tables
from netbox.tables import NetBoxTable, columns
from netbox.forms import NetBoxModelFilterSetForm
from netbox.views.generic import ObjectListView, ObjectView
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField
from utilities.views import register_model_view

from .models import UpgradeJob, DiscoveryPoller
from .upgrade_choices import UpgradeOperationChoices, UpgradeStatusChoices
from .upgrade_filtersets import UpgradeJobFilterSet
from . import upgrade_queue as queue


class ScheduleForm(forms.Form):
    site = DynamicModelChoiceField(queryset=Site.objects.all(), required=False)
    role = DynamicModelChoiceField(queryset=DeviceRole.objects.all(), required=False)
    platform = DynamicModelChoiceField(queryset=Platform.objects.all(), required=False)
    devices = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False,
                                              help_text='Optional explicit devices; combined with the filters above.')
    poller = DynamicModelChoiceField(queryset=DiscoveryPoller.objects.all(), required=False,
                                    help_text='Normally automatic; choose one when devices have multiple poller tags.')
    operation = forms.ChoiceField(choices=UpgradeOperationChoices, initial='audit')
    scheduled_at = forms.DateTimeField(help_text='Include a UTC offset, for example 2026-09-20T22:00:00-04:00.')
    start_before = forms.DateTimeField(help_text='Latest start for device changes. Running jobs continue past this time.')
    profile = forms.CharField(widget=forms.Textarea(attrs={'rows': 15}),
                              help_text='Paste the validated upgrade profile YAML. A copy is saved with every selected device.')
    description = forms.CharField(max_length=200, required=False)

    def clean_profile(self):
        import yaml
        try:
            data = yaml.safe_load(self.cleaned_data['profile'])
            queue.validate_profile(data)
            return data
        except (yaml.YAMLError, ValueError, TypeError) as exc:
            raise forms.ValidationError(str(exc)) from exc

    def schedule_data(self):
        data = dict(self.cleaned_data)
        data['filters'] = {key + '_id': [data[key].pk] for key in ('site', 'role', 'platform') if data[key]}
        if data['devices']:
            data['filters']['id'] = [obj.pk for obj in data['devices']]
        data['poller'] = data['poller'].name if data['poller'] else ''
        return data


class UpgradeScheduleView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.add_upgradejob'
    raise_exception = True

    def get(self, request):
        return render(request, 'netbox_discovery/upgrade_schedule.html', {'form': ScheduleForm()})

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
    last_seen_at = columns.DateTimeColumn()

    class Meta(NetBoxTable.Meta):
        model = UpgradeJob
        fields = ('pk', 'id', 'device_name', 'poller', 'operation', 'status', 'scheduled_at',
                  'start_before', 'stage', 'message', 'last_seen_at', 'description', 'batch_id')
        default_columns = ('device_name', 'poller', 'operation', 'scheduled_at', 'status', 'stage', 'last_seen_at')


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
    actions = ()


@register_model_view(UpgradeJob)
class UpgradeJobView(ObjectView):
    queryset = UpgradeJob.objects.select_related('device', 'poller', 'requested_by')

    def get_extra_context(self, request, instance):
        import json
        return {'profile_text': json.dumps(instance.profile, indent=2),
                'summary_text': json.dumps(instance.summary, indent=2)}


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
