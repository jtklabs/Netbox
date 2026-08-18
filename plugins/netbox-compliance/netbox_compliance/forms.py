"""Forms.

The one piece of real work here is `expected_entries`. It is a JSONField on the
model, and asking an operator to hand-write JSON into a textarea to say "these
five accounts should exist" is how a standard ends up with a typo nobody
notices until a report is wrong. So the form takes plain lines and converts:

    present    one config line per line — the line IS the entry
                   service password-encryption
    exact set  a key, then optional name=value pairs
                   netops privilege=15
                   backupadm privilege=15

The name=value pairs feed the remediation template, which is how
`username {key} privilege {privilege} secret {secret}` gets its privilege
without NetBox holding the secret.

The ConfigCompliance edit form deliberately does not expose `findings`,
`observed`, `pre_change_config` or `remediation_log`. Those are written by the
checker and are the evidence for a verdict; a hand-edited field claiming to be
what a device said is worse than an empty one.
"""

from dcim.models import Device, DeviceRole, Platform, Region, Site
from django import forms
from django.core.exceptions import ValidationError
from extras.models import Tag
from netbox.forms import (
    NetBoxModelBulkEditForm,
    NetBoxModelFilterSetForm,
    NetBoxModelForm,
)
from utilities.forms.fields import (
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    TagFilterField,
)
from utilities.forms.rendering import FieldSet
from utilities.forms.widgets import DatePicker, DateTimePicker

from netbox_compliance.choices import (
    ConfigCheckResultChoices,
    ConfigCheckSourceChoices,
    ConfigCheckTypeChoices,
    ConfigComplianceStatusChoices,
)
from netbox_compliance.models import ConfigCompliance, ConfigStandard

__all__ = (
    'ConfigStandardForm',
    'ConfigStandardFilterForm',
    'ConfigStandardBulkEditForm',
    'ConfigComplianceForm',
    'ConfigComplianceFilterForm',
    'ConfigComplianceBulkEditForm',
    'ComplianceReportForm',
)


def entries_to_text(entries, check_type):
    """Render expected_entries back into the line format the form accepts."""
    lines = []
    for entry in entries or []:
        if isinstance(entry, str):
            lines.append(entry)
            continue
        key = entry.get('key', '')
        variables = entry.get('vars') or {}
        if check_type == ConfigCheckTypeChoices.TYPE_EXACT_SET and variables:
            pairs = ' '.join('%s=%s' % (k, v) for k, v in variables.items())
            lines.append('%s %s' % (key, pairs))
        else:
            lines.append(key)
    return '\n'.join(lines)


def text_to_entries(text, check_type):
    """Parse the line format into the model's list-of-objects shape."""
    entries = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if check_type == ConfigCheckTypeChoices.TYPE_EXACT_SET:
            tokens = line.split()
            key, variables = tokens[0], {}
            for token in tokens[1:]:
                if '=' not in token:
                    raise ValidationError(
                        'In "%s", "%s" is not a name=value pair. An exact-set entry is '
                        'a key followed by optional settings, e.g. '
                        '"netops privilege=15".' % (line, token)
                    )
                name, _, value = token.partition('=')
                variables[name] = value
            entries.append({'key': key, 'vars': variables})
        else:
            # A `present` entry is the whole line, spaces and all.
            entries.append({'key': line, 'vars': {}})
    return entries


class ConfigStandardForm(NetBoxModelForm):
    platforms = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(), required=False,
        help_text='Leave empty to apply to every platform.',
    )
    roles = DynamicModelMultipleChoiceField(
        queryset=DeviceRole.objects.all(), required=False,
        help_text='Leave empty to apply to every device role.',
    )
    sites = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(), required=False,
        help_text='Leave empty to apply to every site.',
    )
    device_tags = DynamicModelMultipleChoiceField(
        queryset=Tag.objects.all(), required=False, label='Device tags',
        help_text='Devices must carry one of these tags. Leave empty for any device.',
    )
    expected_entries = forms.CharField(
        required=False,
        label='Expected entries',
        widget=forms.Textarea(attrs={'rows': 6, 'class': 'font-monospace'}),
        help_text=(
            'One per line. For "must be present" that is the configuration line itself. '
            'For "exact set" it is a key followed by optional name=value settings, '
            'e.g. "netops privilege=15". Leave empty for "must be absent". '
            'Never put a secret here.'
        ),
    )

    fieldsets = (
        FieldSet('name', 'check_type', 'description', name='Standard'),
        FieldSet('match_pattern', 'expected_entries', name='What it governs'),
        FieldSet(
            'auto_remediable', 'allow_enforce', 'add_template', 'remove_template',
            'remediation_notes', name='Remediation',
        ),
        FieldSet('platforms', 'roles', 'sites', 'device_tags', name='Scope'),
        FieldSet('valid_from', 'valid_to', name='In force'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = ConfigStandard
        fields = (
            'name', 'check_type', 'match_pattern', 'expected_entries',
            'add_template', 'remove_template', 'auto_remediable', 'allow_enforce',
            'remediation_notes', 'platforms', 'roles', 'sites', 'device_tags',
            'valid_from', 'valid_to', 'description', 'comments', 'tags',
        )
        widgets = {
            'valid_from': DatePicker(),
            'valid_to': DatePicker(),
            'match_pattern': forms.TextInput(attrs={'class': 'font-monospace'}),
            'add_template': forms.TextInput(attrs={'class': 'font-monospace'}),
            'remove_template': forms.TextInput(attrs={'class': 'font-monospace'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance') or getattr(self, 'instance', None)
        if instance is not None and instance.pk and not self.is_bound:
            self.initial['expected_entries'] = entries_to_text(
                instance.expected_entries, instance.check_type
            )

    def clean_expected_entries(self):
        # self.data rather than cleaned_data: field order is not guaranteed to
        # have cleaned check_type yet, and the parse depends on it.
        check_type = self.data.get('check_type') or self.instance.check_type
        return text_to_entries(self.cleaned_data.get('expected_entries'), check_type)


class ConfigStandardFilterForm(NetBoxModelFilterSetForm):
    model = ConfigStandard

    check_type = forms.MultipleChoiceField(choices=ConfigCheckTypeChoices, required=False)
    platform_id = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(), required=False, label='Platform',
    )
    role_id = DynamicModelMultipleChoiceField(
        queryset=DeviceRole.objects.all(), required=False, label='Device role',
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(), required=False, label='Site',
    )
    auto_remediable = forms.NullBooleanField(
        required=False, label='Automatically remediable',
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')]),
    )
    allow_enforce = forms.NullBooleanField(
        required=False, label='Enforce allowed',
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')]),
    )
    active = forms.NullBooleanField(
        required=False, label='In force today',
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')]),
    )
    tag = TagFilterField(model)


class ConfigStandardBulkEditForm(NetBoxModelBulkEditForm):
    model = ConfigStandard

    valid_to = forms.DateField(required=False, widget=DatePicker())
    auto_remediable = forms.NullBooleanField(
        required=False,
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')]),
    )
    allow_enforce = forms.NullBooleanField(
        required=False,
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')]),
    )
    description = forms.CharField(max_length=200, required=False)

    nullable_fields = ('valid_to', 'description')


class ConfigComplianceForm(NetBoxModelForm):
    device = DynamicModelChoiceField(queryset=Device.objects.all(), selector=True)
    standard = DynamicModelChoiceField(queryset=ConfigStandard.objects.all())

    fieldsets = (
        FieldSet('device', 'standard', 'result', 'error_message', 'source',
                 'last_checked', 'description', name='Result'),
        FieldSet('exempt', 'exempt_reason', 'exempt_approved_by', 'exempt_approved_on',
                 'exempt_review_by', name='Exemption'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = ConfigCompliance
        # No findings/observed/pre_change_config/remediation_log: those are the
        # checker's evidence for its verdict, and a hand-typed value pretending
        # to be what a device said would undermine every report built on them.
        fields = (
            'device', 'standard', 'result', 'error_message', 'source', 'last_checked',
            'exempt', 'exempt_reason', 'exempt_approved_by', 'exempt_approved_on',
            'exempt_review_by', 'description', 'comments', 'tags',
        )
        widgets = {
            'last_checked': DateTimePicker(),
            'exempt_approved_on': DatePicker(),
            'exempt_review_by': DatePicker(),
        }


class ConfigComplianceFilterForm(NetBoxModelFilterSetForm):
    model = ConfigCompliance

    standard_id = DynamicModelMultipleChoiceField(
        queryset=ConfigStandard.objects.all(), required=False, label='Standard',
    )
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(), required=False, label='Device',
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(), required=False, label='Site',
    )
    platform_id = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(), required=False, label='Platform',
    )
    role_id = DynamicModelMultipleChoiceField(
        queryset=DeviceRole.objects.all(), required=False, label='Device role',
    )
    status = forms.MultipleChoiceField(
        choices=ConfigComplianceStatusChoices, required=False, label='Status',
    )
    result = forms.MultipleChoiceField(choices=ConfigCheckResultChoices, required=False)
    source = forms.MultipleChoiceField(choices=ConfigCheckSourceChoices, required=False)
    stale = forms.NullBooleanField(
        required=False, label='Result is stale',
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')]),
    )
    needs_manual_fix = forms.NullBooleanField(
        required=False, label='Needs manual remediation',
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')]),
    )
    tag = TagFilterField(model)


class ConfigComplianceBulkEditForm(NetBoxModelBulkEditForm):
    model = ConfigCompliance

    # Bulk editing exists mostly to grant or lift an exemption across a set of
    # devices at once, which is the realistic operational need. The result
    # itself is deliberately not bulk-editable: a fleet-wide "mark compliant"
    # button is a compliance program's suicide note.
    exempt = forms.NullBooleanField(
        required=False,
        widget=forms.Select(choices=[('', '---------'), (True, 'Yes'), (False, 'No')]),
    )
    exempt_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    exempt_approved_by = forms.CharField(max_length=100, required=False)
    exempt_approved_on = forms.DateField(required=False, widget=DatePicker())
    exempt_review_by = forms.DateField(required=False, widget=DatePicker())
    description = forms.CharField(max_length=200, required=False)

    nullable_fields = (
        'exempt_reason', 'exempt_approved_by', 'exempt_approved_on',
        'exempt_review_by', 'description',
    )


class ComplianceReportForm(forms.Form):
    """Scope for the fleet report. Plain Form — nothing here is saved."""

    region = DynamicModelMultipleChoiceField(
        queryset=Region.objects.all(), required=False,
    )
    site = DynamicModelMultipleChoiceField(queryset=Site.objects.all(), required=False)
    platform = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(), required=False,
    )
    role = DynamicModelMultipleChoiceField(
        queryset=DeviceRole.objects.all(), required=False, label='Device role',
    )
    standard = DynamicModelMultipleChoiceField(
        queryset=ConfigStandard.objects.all(), required=False,
    )
    status = forms.MultipleChoiceField(
        choices=ConfigComplianceStatusChoices, required=False,
        help_text='Leave empty to show every state',
    )
