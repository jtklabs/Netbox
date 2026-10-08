from zoneinfo import available_timezones
from datetime import time
from copy import deepcopy

from django import forms
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Count
from dcim.models import Device, DeviceType, Platform, Region, Site
from tenancy.models import Tenant
from extras.models import Tag
import django_tables2 as tables
from netbox.filtersets import NetBoxModelFilterSet
from netbox.forms import NetBoxModelForm, NetBoxModelFilterSetForm
from netbox.tables import NetBoxTable, columns
from netbox.object_actions import AddObject, EditObject, DeleteObject
from netbox.views.generic import ObjectListView, ObjectView, ObjectEditView, ObjectDeleteView
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField
from utilities.forms.rendering import FieldSet
from utilities.views import register_model_view

from .models import AuditSchedule, AuditRun, JobProfile, UpgradeJob
from .profile_views import RemediationWidget
from .upgrade_choices import REMEDIATION_FEATURES
from . import upgrade_queue as queue
from .audit_scheduling import validate_profile_access


SCOPE_FIELDS = {'regions': 'region_id', 'tenants': 'tenant_id', 'sites': 'site_id', 'platforms': 'platform_id',
                'models': 'device_type_id', 'device_tags': 'tag_id', 'devices': 'id'}
SCOPE_MODELS = {'region_id': ('Regions', Region), 'tenant_id': ('Tenants', Tenant), 'site_id': ('Sites', Site),
                'platform_id': ('Platforms', Platform), 'device_type_id': ('Models', DeviceType),
                'tag_id': ('Device tags', Tag), 'id': ('Devices', Device)}


class AuditScheduleForm(NetBoxModelForm):
    all_active = forms.BooleanField(required=False, label='All active devices')
    regions = DynamicModelMultipleChoiceField(queryset=Region.objects.all(), required=False)
    tenants = DynamicModelMultipleChoiceField(queryset=Tenant.objects.all(), required=False)
    sites = DynamicModelMultipleChoiceField(queryset=Site.objects.all(), required=False)
    platforms = DynamicModelMultipleChoiceField(queryset=Platform.objects.all(), required=False)
    models = DynamicModelMultipleChoiceField(queryset=DeviceType.objects.all(), required=False)
    device_tags = DynamicModelMultipleChoiceField(queryset=Tag.objects.all(), required=False)
    devices = DynamicModelMultipleChoiceField(queryset=Device.objects.all(), required=False)
    saved_profile = DynamicModelChoiceField(queryset=JobProfile.objects.filter(kind='remediate'), required=False,
                                            query_params={'kind': 'remediate'})
    features = forms.MultipleChoiceField(choices=REMEDIATION_FEATURES, widget=RemediationWidget,
                                         required=False, label='Standards')
    comparison = forms.ChoiceField(choices=(('replace', 'Exact match'), ('add', 'Required entries present')), required=False)
    time_zone = forms.ChoiceField(choices=[(zone, zone) for zone in sorted(available_timezones())], required=False)
    local_time = forms.TimeField(widget=forms.TimeInput(attrs={'type': 'time'}), label='Start time', required=False)
    allow_clearpass_cluster_changes = forms.BooleanField(required=False, label='Allow ClearPass cluster-wide changes')
    filters = forms.JSONField(required=False, widget=forms.HiddenInput)
    profile = forms.JSONField(required=False, widget=forms.HiddenInput)
    fieldsets = (
        FieldSet('name', 'enabled', 'description', name='Audit schedule'),
        FieldSet('frequency', 'weekday', 'local_time', 'time_zone', 'window_hours', name='Timing'),
        FieldSet('all_active', 'regions', 'tenants', 'sites', 'platforms', 'models', 'device_tags', 'devices', name='Device scope'),
        FieldSet('remediate', 'profile_source', 'saved_profile', 'features', 'comparison',
                 'allow_clearpass_cluster_changes', name='Standards'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = AuditSchedule
        fields = ('name', 'enabled', 'remediate', 'frequency', 'weekday', 'local_time', 'time_zone', 'window_hours',
                  'filters', 'profile_source', 'saved_profile', 'profile', 'description', 'comments', 'tags')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['weekday'].required = False
        self.initial.setdefault('time_zone', settings.TIME_ZONE)
        self.initial['comparison'] = self.instance.profile.get('mode', 'replace')
        self.initial['features'] = self.instance.profile.get('features', [])
        self.initial['allow_clearpass_cluster_changes'] = self.instance.profile.get('allow_clearpass_cluster_changes', False)
        self.initial['all_active'] = self.instance.filters == {'status': ['active']}
        for field, key in SCOPE_FIELDS.items():
            self.initial[field] = self.instance.filters.get(key, [])

    def clean(self):
        super().clean()
        data = self.cleaned_data
        if data.get('frequency') != 'now':
            for field in ('local_time', 'time_zone'):
                if not data.get(field):
                    self.add_error(field, 'This field is required for recurring schedules.')
        data['local_time'] = data.get('local_time') or time(2)
        data['time_zone'] = data.get('time_zone') or settings.TIME_ZONE
        if data.get('frequency') == 'weekly' and data.get('weekday') is None:
            self.add_error('weekday', 'Choose a weekday for weekly schedules.')
        data['weekday'] = data.get('weekday') or 0
        filters = {'status': ['active']}
        for field, key in SCOPE_FIELDS.items():
            if data.get(field):
                filters[key] = [obj.pk for obj in data[field]]
        if len(filters) == 1 and not data.get('all_active'):
            self.add_error('all_active', 'Choose a device scope or explicitly select all active devices.')
        if len(filters) > 1 and data.get('all_active'):
            self.add_error('all_active', 'Clear All active devices to use a narrower scope.')
        data['filters'] = self.instance.filters = filters
        source = data.get('profile_source')
        profile = {'features': data.get('features', []), 'mode': data.get('comparison') or 'replace'} if source == 'custom' else {}
        if source == 'custom' and data.get('remediate') and data.get('allow_clearpass_cluster_changes'):
            profile['allow_clearpass_cluster_changes'] = True
        if source == 'custom' and not profile['features']:
            self.add_error('features', 'Choose at least one standard to audit.')
        data['profile'] = self.instance.profile = profile
        if source != 'saved':
            data['saved_profile'] = None
        if (data.get('enabled') or data.get('remediate')) and not self.errors:
            candidate = AuditSchedule(**{key: data[key] for key in (
                'name', 'remediate', 'filters', 'profile_source', 'profile', 'saved_profile', 'window_hours')})
            try:
                validate_profile_access(candidate, self.instance.run_as)
            except queue.QueueError as exc:
                self.add_error(None, str(exc))
        return data


class AuditScheduleFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = AuditSchedule
        fields = ('id', 'name', 'enabled', 'remediate', 'frequency', 'run_as_id', 'profile_source', 'saved_profile_id')

    def search(self, queryset, name, value):
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))


class AuditScheduleFilterForm(NetBoxModelFilterSetForm):
    model = AuditSchedule
    enabled = forms.NullBooleanField(required=False)


class AuditRunFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = AuditRun
        fields = ('id', 'schedule_id', 'outcome')

    def search(self, queryset, name, value):
        return queryset.filter(Q(schedule__name__icontains=value) | Q(message__icontains=value))


class AuditScheduleTable(NetBoxTable):
    name = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()
    remediate = columns.BooleanColumn(verbose_name='Remediate')
    overdue = columns.BooleanColumn(orderable=False)
    next_run_at = columns.DateTimeColumn()
    last_run_at = columns.DateTimeColumn()

    class Meta(NetBoxTable.Meta):
        model = AuditSchedule
        fields = ('pk', 'name', 'enabled', 'remediate', 'frequency', 'local_time', 'time_zone', 'next_run_at', 'last_run_at', 'overdue', 'run_as')
        default_columns = ('name', 'enabled', 'remediate', 'frequency', 'local_time', 'time_zone', 'next_run_at', 'last_run_at', 'overdue')


class AuditRunTable(NetBoxTable):
    actions = columns.ActionsColumn(actions=('changelog',))
    scheduled_for = columns.DateTimeColumn(linkify=True)
    schedule = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = AuditRun
        fields = ('id', 'schedule', 'scheduled_for', 'dispatched_at', 'outcome', 'job_count', 'message')
        default_columns = ('schedule', 'scheduled_for', 'outcome', 'job_count', 'message')


@register_model_view(AuditSchedule, name='list')
class AuditScheduleListView(ObjectListView):
    queryset = AuditSchedule.objects.select_related('run_as')
    table = AuditScheduleTable
    filterset = AuditScheduleFilterSet
    filterset_form = AuditScheduleFilterForm
    actions = (AddObject,)


@register_model_view(AuditSchedule)
class AuditScheduleView(ObjectView):
    queryset = AuditSchedule.objects.select_related('run_as', 'saved_profile')
    actions = (EditObject, DeleteObject)

    def get_extra_context(self, request, instance):
        runs = instance.runs.restrict(request.user, 'view')[:50]
        scope = []
        for key, values in instance.filters.items():
            if key == 'status':
                scope.append(('Status', ', '.join(values)))
            else:
                label, model = SCOPE_MODELS[key]
                objects = model.objects.restrict(request.user, 'view').filter(pk__in=values)
                scope.append((label, ', '.join(str(obj) for obj in objects) or 'No visible matches'))
        return {'runs': runs, 'scope': scope}


@register_model_view(AuditSchedule, 'edit')
class AuditScheduleEditView(ObjectEditView):
    queryset = AuditSchedule.objects.all()
    form = AuditScheduleForm
    template_name = 'netbox_discovery/auditschedule_edit.html'

    def alter_object(self, obj, request, url_args, url_kwargs):
        if not request.user.has_perm('netbox_discovery.add_upgradejob'):
            raise PermissionDenied('Scheduling audits requires permission to add device jobs.')
        obj.run_as = request.user
        if not obj.pk and request.method == 'GET':
            if request.GET.get('frequency') == 'now':
                obj.frequency = 'now'
            obj.remediate = request.GET.get('operation') == 'remediate'
            filters = {}
            for param, key in (('devices', 'id'), ('device_type', 'device_type_id')):
                values = [int(value) for value in request.GET.getlist(param) if value.isdigit()]
                if values:
                    filters[key] = values
            obj.filters = filters
            features = [value for value, _ in REMEDIATION_FEATURES if value in request.GET.getlist('features')]
            obj.profile_source = 'custom' if features else 'model'
            obj.profile = {'features': features, 'mode': 'replace'} if features else {}
            source = request.GET.get('from_job', '')
            if source.isdigit():
                job = UpgradeJob.objects.restrict(request.user, 'view').filter(
                    pk=int(source), operation__in=('audit_config', 'remediate')).first()
                if job:
                    obj.frequency = 'now'
                    obj.remediate = job.operation == 'remediate'
                    obj.filters = {'id': [job.device_id]}
                    obj.profile_source = 'custom'
                    obj.profile = deepcopy(job.profile)
        return obj


@register_model_view(AuditSchedule, 'delete')
class AuditScheduleDeleteView(ObjectDeleteView):
    queryset = AuditSchedule.objects.all()


@register_model_view(AuditRun, name='list')
class AuditRunListView(ObjectListView):
    queryset = AuditRun.objects.select_related('schedule')
    table = AuditRunTable
    filterset = AuditRunFilterSet
    actions = ()


@register_model_view(AuditRun)
class AuditRunView(ObjectView):
    queryset = AuditRun.objects.select_related('schedule')
    actions = ()

    def get_extra_context(self, request, instance):
        jobs = instance.jobs.restrict(request.user, 'view').select_related('device')
        return {'jobs': jobs, 'counts': jobs.values('status').annotate(count=Count('pk')).order_by('status')}
