"""Saved job profiles and defaults for device models."""
import yaml
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from dcim.models import DeviceType, Platform
import django_tables2 as tables
from netbox.forms import NetBoxModelForm, NetBoxModelFilterSetForm
from netbox.tables import NetBoxTable
from netbox.object_actions import AddObject, EditObject, DeleteObject
from netbox.views.generic import ObjectListView, ObjectView, ObjectEditView, ObjectDeleteView
from utilities.forms.fields import DynamicModelChoiceField, DynamicModelMultipleChoiceField
from utilities.views import register_model_view
from utilities.exceptions import AbortRequest

from .models import JobProfile, DeviceTypeProfile, PlatformProfile
from .profile_filtersets import JobProfileFilterSet, DeviceTypeProfileFilterSet, PlatformProfileFilterSet
from .upgrade_choices import REMEDIATION_FEATURES, REMEDIATION_MODES
from .upgrade_queue import validate_profile
from .profile_assignments import (AssignmentConflict, assign_models, selected_models, validate_selection,
                                  validate_model_replacement)


UPGRADE_TEMPLATE = '''# Example only: replace with your approved upgrade path.
name: ""
starting_versions:
  - "17.9.4a"
  - "17.12.3"
target_version: "17.12.4"
image: ""  # Image filename (.bin, .swi or .iso)
md5: ""  # Vendor's 32-character checksum
minimum_free_bytes: 1500000000
# Optional source URL; empty if already staged.
image_source: ""
bundle_conversion_validated: false
'''


class ProfileDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, indentless=False)


def profile_yaml(plan):
    return yaml.dump(plan, Dumper=ProfileDumper, sort_keys=False,
                     default_flow_style=False, indent=2, width=1000)


class RemediationWidget(forms.CheckboxSelectMultiple):
    template_name = 'netbox_discovery/widgets/remediation.html'


class JobProfileForm(NetBoxModelForm):
    device_types = DynamicModelMultipleChoiceField(queryset=DeviceType.objects.all(), required=False,
                                                   label='Assigned models')
    replace_existing = forms.BooleanField(required=False, label='Replace existing assignments')
    assignment_confirmation = forms.CharField(required=False, widget=forms.HiddenInput)
    upgrade_yaml = forms.CharField(label='Upgrade settings', required=False,
                                   widget=forms.Textarea(attrs={
                                       'rows': 18, 'class': 'font-monospace', 'wrap': 'off',
                                       'spellcheck': 'false', 'autocapitalize': 'off',
                                       'autocomplete': 'off',
                                       'style': 'tab-size: 2; line-height: 1.5; white-space: pre;',
                                   }))
    skip_running_config_check = forms.BooleanField(
        label='Skip running-config matching', required=False,
    )
    drain_connections = forms.BooleanField(
        label='Drain F5 connections before reboot', required=False,
        help_text='Standalone BIG-IP only; HA/cluster members are blocked. Temporarily disable virtual servers '
                  'and wait for existing connections to settle. Original states are restored after reboot or on failure.')
    drain_min_reduction_percent = forms.IntegerField(
        label='F5 drain minimum reduction (%)', required=False, initial=5, min_value=1, max_value=100,
        help_text='Finish when connections decrease by less than this percentage over a full observation window.')
    drain_window_seconds = forms.IntegerField(
        label='F5 drain observation window (seconds)', required=False, initial=60, min_value=1, max_value=86400)
    drain_timeout_seconds = forms.IntegerField(
        label='F5 drain maximum wait (seconds)', required=False, initial=900, min_value=1, max_value=86400,
        help_text='If connections keep falling or increase until this limit, restore VIPs and cancel the reboot.')
    features = forms.MultipleChoiceField(choices=REMEDIATION_FEATURES, required=False,
                                         widget=RemediationWidget, label='Remediate')
    remediation_mode = forms.ChoiceField(choices=REMEDIATION_MODES, required=False, initial='add')
    allow_clearpass_cluster_changes = forms.BooleanField(
        label='Allow ClearPass cluster-wide changes', required=False)

    class Meta:
        model = JobProfile
        fields = ('name', 'kind', 'device_types', 'replace_existing', 'assignment_confirmation',
                  'upgrade_yaml', 'skip_running_config_check', 'drain_connections', 'drain_min_reduction_percent',
                  'drain_window_seconds', 'drain_timeout_seconds', 'features',
                  'remediation_mode', 'allow_clearpass_cluster_changes', 'description', 'comments', 'tags')

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.user = self.user or getattr(self.instance, '_assignment_user', None)
        if not self.instance.pk and not self.is_bound:
            self.initial.setdefault('upgrade_yaml', UPGRADE_TEMPLATE)
        if self.instance.pk:
            self.initial['device_types'] = list(selected_models(self.instance).values_list('pk', flat=True))
            if self.instance.kind == 'remediate':
                self.initial.update(features=self.instance.plan['features'],
                                    remediation_mode=self.instance.plan['mode'],
                                    allow_clearpass_cluster_changes=self.instance.plan.get(
                                        'allow_clearpass_cluster_changes', False))
            else:
                plan = dict(self.instance.plan)
                plan.pop('models', None)
                self.initial['skip_running_config_check'] = plan.pop('skip_running_config_check', False)
                for key, default in (('drain_connections', False), ('drain_min_reduction_percent', 5),
                                     ('drain_window_seconds', 60), ('drain_timeout_seconds', 900)):
                    self.initial[key] = plan.pop(key, default)
                self.initial['upgrade_yaml'] = profile_yaml(plan)

    def clean(self):
        super().clean()
        data = self.cleaned_data
        kind = data.get('kind')
        field = 'features' if kind == 'remediate' else 'upgrade_yaml'
        try:
            plan = ({'features': data.get('features', []), 'mode': data.get('remediation_mode') or 'add'}
                    if kind == 'remediate' else yaml.safe_load(data.get('upgrade_yaml', '')))
            if kind == 'remediate' and data.get('allow_clearpass_cluster_changes'):
                plan['allow_clearpass_cluster_changes'] = True
            if kind == 'upgrade' and isinstance(plan, dict):
                # The form controls own these settings; raw API/CLI profiles use the same keys.
                for key in ('drain_connections', 'drain_min_reduction_percent',
                            'drain_window_seconds', 'drain_timeout_seconds'):
                    plan.pop(key, None)
                if data.get('drain_connections'):
                    plan['drain_connections'] = True
                    for key, default in (('drain_min_reduction_percent', 5), ('drain_window_seconds', 60),
                                         ('drain_timeout_seconds', 900)):
                        plan[key] = data.get(key) if data.get(key) is not None else default
            check_plan = dict(plan) if isinstance(plan, dict) else plan
            if kind == 'upgrade' and isinstance(check_plan, dict):
                check_plan['models'] = ['assigned-models']
            validate_profile(check_plan, kind)
            if kind == 'upgrade':
                plan.pop('models', None)
                if data.get('skip_running_config_check'):
                    plan['skip_running_config_check'] = True
                else:
                    plan.pop('skip_running_config_check', None)
            self.instance.plan = plan
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            self.add_error(field, str(exc))
        self.instance.kind = kind
        if 'device_types' in data and kind in ('upgrade', 'remediate'):
            try:
                validate_selection(self.instance, data['device_types'], self.user,
                                   data.get('replace_existing', False), data.get('assignment_confirmation', ''))
            except AssignmentConflict as exc:
                self.data = self.data.copy()
                self.data[self.add_prefix('assignment_confirmation')] = exc.confirmation
                self.add_error('device_types', exc)
            except ValidationError as exc:
                self.add_error('device_types', exc)
        return data

    @transaction.atomic
    def save(self, commit=True):
        return super().save(commit=commit)

    def _save_m2m(self):
        super()._save_m2m()
        try:
            assign_models(self.instance, self.cleaned_data['device_types'], self.user,
                          self.cleaned_data.get('replace_existing', False),
                          self.cleaned_data.get('assignment_confirmation', ''))
        except ValidationError as exc:
            raise AbortRequest('; '.join(exc.messages)) from exc


class DeviceTypeProfileForm(NetBoxModelForm):
    device_type = DynamicModelChoiceField(queryset=DeviceType.objects.all(), label='Model')
    upgrade_profile = DynamicModelChoiceField(queryset=JobProfile.objects.filter(kind='upgrade'), required=False,
                                              query_params={'kind': 'upgrade'})
    remediation_profile = DynamicModelChoiceField(queryset=JobProfile.objects.filter(kind='remediate'), required=False,
                                                  query_params={'kind': 'remediate'})
    replace_existing = forms.BooleanField(required=False, label='Replace existing assignments')

    class Meta:
        model = DeviceTypeProfile
        fields = ('device_type', 'upgrade_profile', 'remediation_profile', 'replace_existing',
                  'description', 'comments', 'tags')

    def clean(self):
        super().clean()
        data = self.cleaned_data
        candidate = DeviceTypeProfile(pk=self.instance.pk,
                                       upgrade_profile=data.get('upgrade_profile'),
                                       remediation_profile=data.get('remediation_profile'))
        validate_model_replacement(candidate, data.get('replace_existing', False))
        return data

    @transaction.atomic
    def save(self, commit=True):
        if commit:
            list(DeviceType.objects.select_for_update().filter(pk=self.instance.device_type_id))
            try:
                validate_model_replacement(self.instance, self.cleaned_data.get('replace_existing', False))
            except ValidationError as exc:
                raise AbortRequest('; '.join(exc.messages)) from exc
        return super().save(commit=commit)


class JobProfileFilterForm(NetBoxModelFilterSetForm):
    model = JobProfile
    kind = forms.ChoiceField(choices=(('', '---------'), *JobProfile._meta.get_field('kind').choices), required=False)


class JobProfileTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = JobProfile
        fields = ('pk', 'id', 'name', 'kind', 'description')
        default_columns = ('name', 'kind', 'description')


class DeviceTypeProfileTable(NetBoxTable):
    device_type = tables.Column(linkify=lambda record: record.get_absolute_url(), verbose_name='Model')
    upgrade_profile = tables.Column(linkify=True)
    remediation_profile = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = DeviceTypeProfile
        fields = ('pk', 'id', 'device_type', 'upgrade_profile', 'remediation_profile', 'description')
        default_columns = ('device_type', 'upgrade_profile', 'remediation_profile')


@register_model_view(JobProfile, name='list')
class JobProfileListView(ObjectListView):
    queryset = JobProfile.objects.all()
    table = JobProfileTable
    filterset = JobProfileFilterSet
    filterset_form = JobProfileFilterForm
    actions = (AddObject,)


@register_model_view(JobProfile)
class JobProfileView(ObjectView):
    queryset = JobProfile.objects.all()
    actions = (EditObject, DeleteObject)

    def get_extra_context(self, request, instance):
        from django.db.models import Q
        return {'plan_yaml': profile_yaml(instance.resolved_plan()),
                'platform_assignments': PlatformProfile.objects.restrict(request.user, 'view').filter(
                    remediation_profile=instance).select_related('platform'),
                'assignments': DeviceTypeProfile.objects.restrict(request.user, 'view').filter(
                    Q(upgrade_profile=instance) | Q(remediation_profile=instance)).select_related('device_type')}


@register_model_view(JobProfile, 'edit')
class JobProfileEditView(ObjectEditView):
    queryset = JobProfile.objects.all()
    form = JobProfileForm
    template_name = 'netbox_discovery/jobprofile_edit.html'

    def alter_object(self, obj, request, url_args, url_kwargs):
        obj._assignment_user = request.user
        return obj


@register_model_view(JobProfile, 'delete')
class JobProfileDeleteView(ObjectDeleteView):
    queryset = JobProfile.objects.all()


@register_model_view(DeviceTypeProfile, name='list')
class DeviceTypeProfileListView(ObjectListView):
    queryset = DeviceTypeProfile.objects.select_related('device_type__manufacturer', 'upgrade_profile', 'remediation_profile')
    table = DeviceTypeProfileTable
    filterset = DeviceTypeProfileFilterSet
    actions = (AddObject,)


@register_model_view(DeviceTypeProfile)
class DeviceTypeProfileView(ObjectView):
    queryset = DeviceTypeProfile.objects.select_related('device_type', 'upgrade_profile', 'remediation_profile')
    actions = (EditObject, DeleteObject)


@register_model_view(DeviceTypeProfile, 'edit')
class DeviceTypeProfileEditView(ObjectEditView):
    queryset = DeviceTypeProfile.objects.all()
    form = DeviceTypeProfileForm

    def dispatch(self, request, *args, **kwargs):
        # "Assign to model" may replace a default without creating a second
        # one-to-one assignment. Route through the normal edit permission and
        # change-log handling, including its save-time object restrictions.
        if request.method == 'POST' and not kwargs and request.user.is_authenticated:
            replace = forms.BooleanField(required=False).clean(request.POST.get('replace_existing'))
            try:
                model_id = forms.IntegerField(min_value=1).clean(request.POST.get('device_type'))
            except ValidationError:
                model_id = None
            if replace and model_id:
                with transaction.atomic():
                    list(DeviceType.objects.select_for_update().filter(pk=model_id))
                    existing = DeviceTypeProfile.objects.filter(device_type_id=model_id).first()
                    if existing:
                        self._replacement_from_add = True
                        return super().dispatch(request, *args, pk=existing.pk)
        return super().dispatch(request, *args, **kwargs)

    def alter_object(self, obj, request, url_args, url_kwargs):
        if getattr(self, '_replacement_from_add', False):
            # Blank fields on the add form mean "leave unchanged". Explicit
            # clearing remains available on the regular edit page.
            initial = self.form(instance=obj)
            data = request.POST.copy()
            for name in initial.fields:
                if name not in data or data.get(name) == '':
                    value = initial[name].value()
                    if isinstance(value, (list, tuple)):
                        data.setlist(name, value)
                    elif value is not None:
                        data[name] = value
            request.POST = data
        return obj


@register_model_view(DeviceTypeProfile, 'delete')
class DeviceTypeProfileDeleteView(ObjectDeleteView):
    queryset = DeviceTypeProfile.objects.all()


class PlatformProfileForm(NetBoxModelForm):
    platform = DynamicModelChoiceField(queryset=Platform.objects.all())
    remediation_profile = DynamicModelChoiceField(queryset=JobProfile.objects.filter(kind='remediate'),
                                                  query_params={'kind': 'remediate'}, label='Standards profile')
    replace_existing = forms.BooleanField(required=False, label='Replace existing assignments')

    class Meta:
        model = PlatformProfile
        fields = ('platform', 'remediation_profile', 'replace_existing', 'description', 'comments', 'tags')

    def clean(self):
        super().clean()
        data = self.cleaned_data
        candidate = PlatformProfile(pk=self.instance.pk, remediation_profile=data.get('remediation_profile'))
        validate_model_replacement(candidate, data.get('replace_existing', False), fields=('remediation_profile_id',))
        return data

    @transaction.atomic
    def save(self, commit=True):
        if commit:
            list(Platform.objects.select_for_update().filter(pk=self.instance.platform_id))
            try:
                validate_model_replacement(self.instance, self.cleaned_data.get('replace_existing', False),
                                           fields=('remediation_profile_id',))
            except ValidationError as exc:
                raise AbortRequest('; '.join(exc.messages)) from exc
        return super().save(commit=commit)


class PlatformProfileTable(NetBoxTable):
    platform = tables.Column(linkify=lambda record: record.get_absolute_url())
    remediation_profile = tables.Column(linkify=True, verbose_name='Standards profile')

    class Meta(NetBoxTable.Meta):
        model = PlatformProfile
        fields = ('pk', 'id', 'platform', 'remediation_profile', 'description')
        default_columns = ('platform', 'remediation_profile', 'description')


@register_model_view(PlatformProfile, name='list')
class PlatformProfileListView(ObjectListView):
    queryset = PlatformProfile.objects.select_related('platform', 'remediation_profile')
    table = PlatformProfileTable
    filterset = PlatformProfileFilterSet
    actions = (AddObject,)


@register_model_view(PlatformProfile)
class PlatformProfileView(ObjectView):
    queryset = PlatformProfile.objects.select_related('platform', 'remediation_profile')
    actions = (EditObject, DeleteObject)


@register_model_view(PlatformProfile, 'edit')
class PlatformProfileEditView(ObjectEditView):
    queryset = PlatformProfile.objects.all()
    form = PlatformProfileForm


@register_model_view(PlatformProfile, 'delete')
class PlatformProfileDeleteView(ObjectDeleteView):
    queryset = PlatformProfile.objects.all()
