from dcim.models import (
    Device,
    DeviceRole,
    DeviceType,
    Manufacturer,
    ModuleType,
    Platform,
    Site,
)
from django import forms
from django.contrib.contenttypes.models import ContentType
from netbox.forms import (
    NetBoxModelBulkEditForm,
    NetBoxModelFilterSetForm,
    NetBoxModelForm,
    NetBoxModelImportForm,
)
from utilities.forms.fields import (
    CSVChoiceField,
    CSVModelChoiceField,
    DynamicModelChoiceField,
    DynamicModelMultipleChoiceField,
    TagFilterField,
)
from utilities.forms.rendering import FieldSet, TabbedGroups
from utilities.forms.widgets import DatePicker, DateTimePicker

from netbox_refresh.choices import (
    ChecksumTypeChoices,
    ComplianceStatusChoices,
    LifecycleSourceChoices,
    SoftwareSourceChoices,
)
from netbox_refresh.models import (
    DeviceSoftware,
    ModelLifecycle,
    SoftwareStandard,
    SoftwareVersion,
)

__all__ = (
    'ModelLifecycleForm',
    'ModelLifecycleFilterForm',
    'ModelLifecycleImportForm',
    'ModelLifecycleBulkEditForm',
    'RefreshReportForm',
    'SoftwareVersionForm',
    'SoftwareVersionFilterForm',
    'SoftwareVersionImportForm',
    'SoftwareVersionBulkEditForm',
    'SoftwareStandardForm',
    'SoftwareStandardFilterForm',
    'SoftwareStandardBulkEditForm',
    'DeviceSoftwareForm',
    'DeviceSoftwareFilterForm',
    'DeviceSoftwareImportForm',
    'DeviceSoftwareBulkEditForm',
    'ComplianceReportForm',
)

DATE_FIELDS = (
    'announcement_date',
    'end_of_sale',
    'end_of_sw_maintenance',
    'end_of_security_support',
    'end_of_routine_failure_analysis',
    'end_of_service_attach',
    'end_of_service_contract_renewal',
    'end_of_support',
)


class ModelLifecycleForm(NetBoxModelForm):
    device_type = DynamicModelChoiceField(
        queryset=DeviceType.objects.all(), required=False, selector=True,
    )
    module_type = DynamicModelChoiceField(
        queryset=ModuleType.objects.all(), required=False, selector=True,
    )
    replacement_device_type = DynamicModelChoiceField(
        queryset=DeviceType.objects.all(), required=False, selector=True,
        label='Replacement device type',
    )
    replacement_module_type = DynamicModelChoiceField(
        queryset=ModuleType.objects.all(), required=False, selector=True,
        label='Replacement module type',
    )

    fieldsets = (
        FieldSet(
            TabbedGroups(
                FieldSet('device_type', name='Device Type'),
                FieldSet('module_type', name='Module Type'),
            ),
            name='Hardware model',
        ),
        FieldSet(*DATE_FIELDS, name='Lifecycle dates'),
        FieldSet(
            TabbedGroups(
                FieldSet('replacement_device_type', name='Device Type'),
                FieldSet('replacement_module_type', name='Module Type'),
            ),
            'replacement_notes',
            name='Replacement',
        ),
        FieldSet('replacement_cost', 'currency', 'cost_updated', name='Cost'),
        FieldSet('bulletin_number', 'bulletin_url', 'source', 'description', name='Reference'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = ModelLifecycle
        fields = DATE_FIELDS + (
            'replacement_device_type', 'replacement_module_type', 'replacement_notes',
            'replacement_cost', 'currency', 'cost_updated',
            'bulletin_number', 'bulletin_url', 'source',
            'description', 'comments', 'tags',
        )
        widgets = dict(
            {field: DatePicker() for field in DATE_FIELDS},
            cost_updated=DatePicker(),
        )

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.get('initial', {}).copy()
        if instance is not None and instance.assigned_object:
            field = ('device_type' if instance.assigned_object._meta.model_name == 'devicetype'
                     else 'module_type')
            initial[field] = instance.assigned_object
        kwargs['initial'] = initial
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        device_type = self.cleaned_data.get('device_type')
        module_type = self.cleaned_data.get('module_type')
        if device_type and module_type:
            raise forms.ValidationError(
                'Select a device type or a module type, not both.'
            )
        target = device_type or module_type
        if target is None:
            raise forms.ValidationError('Select the hardware model this lifecycle applies to.')
        self.instance.assigned_object = target


class ModelLifecycleFilterForm(NetBoxModelFilterSetForm):
    model = ModelLifecycle
    manufacturer_id = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False, label='Manufacturer',
    )
    end_of_support__gte = forms.DateField(
        required=False, widget=DatePicker(), label='End of support after',
    )
    end_of_support__lte = forms.DateField(
        required=False, widget=DatePicker(), label='End of support before',
    )
    end_of_sale__gte = forms.DateField(
        required=False, widget=DatePicker(), label='End of sale after',
    )
    end_of_sale__lte = forms.DateField(
        required=False, widget=DatePicker(), label='End of sale before',
    )
    source = forms.MultipleChoiceField(choices=LifecycleSourceChoices, required=False)
    has_replacement = forms.NullBooleanField(required=False)
    has_cost = forms.NullBooleanField(required=False)
    tag = TagFilterField(model)


# Accepted in an uploaded CSV, in order. US format first because that is what
# comes out of a spreadsheet here; ISO second because it is unambiguous and any
# CSV written before this still has to load. Day-first is deliberately absent:
# 03/04/2026 would parse silently as a different date under it, and a silently
# wrong end-of-support date is worse than a rejected row.
CSV_DATE_INPUT_FORMATS = ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d')


class ModelLifecycleImportForm(NetBoxModelImportForm):
    device_type = CSVModelChoiceField(
        queryset=DeviceType.objects.all(), to_field_name='model', required=False,
        help_text='Device type model name',
    )
    module_type = CSVModelChoiceField(
        queryset=ModuleType.objects.all(), to_field_name='model', required=False,
        help_text='Module type model name',
    )
    replacement_device_type = CSVModelChoiceField(
        queryset=DeviceType.objects.all(), to_field_name='model', required=False,
    )
    replacement_module_type = CSVModelChoiceField(
        queryset=ModuleType.objects.all(), to_field_name='model', required=False,
    )
    source = CSVChoiceField(choices=LifecycleSourceChoices, required=False)

    def __init__(self, *args, headers=None, **kwargs):
        super().__init__(*args, headers=headers, **kwargs)
        # Keep only the columns the sheet actually has. NetBox's `headers`
        # sets to_field_name and nothing else, so every form field stays live
        # whether or not the CSV mentions it — which has two consequences,
        # both wrong for this form.
        #
        # A sheet without a `currency` column failed on "This field is
        # required", though the model has a default; and on an update, a field
        # with no column arrived empty and construct_instance would write that
        # emptiness over a value already recorded. A revised bulletin carrying
        # one date would have wiped the other seven.
        #
        # Dropping the field keeps it out of cleaned_data, which is what
        # construct_instance checks, so the stored value stands.
        if self.headers:
            for name in [f for f in self.fields if f not in self.headers]:
                del self.fields[name]

    # Declared explicitly so every lifecycle date takes a US-formatted value.
    # Generated from DATE_FIELDS rather than typed out eight times, so a field
    # added there cannot quietly go back to ISO-only.
    locals().update({
        name: forms.DateField(required=False, input_formats=CSV_DATE_INPUT_FORMATS)
        for name in DATE_FIELDS
    })
    cost_updated = forms.DateField(required=False, input_formats=CSV_DATE_INPUT_FORMATS)

    class Meta:
        model = ModelLifecycle
        fields = ('device_type', 'module_type') + DATE_FIELDS + (
            'replacement_device_type', 'replacement_module_type', 'replacement_notes',
            'replacement_cost', 'currency', 'cost_updated',
            'bulletin_number', 'bulletin_url', 'source', 'description', 'comments', 'tags',
        )

    def clean(self):
        super().clean()
        target = self.cleaned_data.get('device_type') or self.cleaned_data.get('module_type')
        if target is None:
            raise forms.ValidationError('Provide either device_type or module_type.')

        # Update the record this model already has rather than refusing the
        # row. A lifecycle record is unique per hardware model, so importing a
        # spreadsheet that mentions anything already loaded used to fail the
        # whole upload on "This hardware model already has a lifecycle record"
        # -- which makes a vendor's EoX bulletin, where most rows are dates
        # that have merely been revised, impossible to load without first
        # weeding out by hand every model already present.
        #
        # Switching self.instance here is what makes it an update: _post_clean
        # runs after this and builds onto whatever instance is set, so the save
        # becomes an UPDATE and the unique check excludes the row itself.
        #
        # Only columns present in the CSV are touched -- see __init__, which
        # is what makes that true. A sheet carrying just end_of_support
        # revises that and leaves the other dates, the cost and the bulletin
        # alone, which is what a revision usually is.
        if not self.instance.pk:
            existing = ModelLifecycle.objects.filter(
                assigned_object_type=ContentType.objects.get_for_model(target),
                assigned_object_id=target.pk,
            ).first()
            if existing is not None:
                self.instance = existing

        self.instance.assigned_object = target


class ModelLifecycleBulkEditForm(NetBoxModelBulkEditForm):
    model = ModelLifecycle
    end_of_sale = forms.DateField(required=False, widget=DatePicker())
    end_of_support = forms.DateField(required=False, widget=DatePicker())
    replacement_cost = forms.DecimalField(required=False, max_digits=12, decimal_places=2)
    currency = forms.CharField(required=False, max_length=3)
    cost_updated = forms.DateField(required=False, widget=DatePicker())
    replacement_device_type = DynamicModelChoiceField(
        queryset=DeviceType.objects.all(), required=False,
    )
    description = forms.CharField(required=False, max_length=200)

    nullable_fields = ('replacement_cost', 'cost_updated', 'replacement_device_type',
                       'description')


class RefreshReportForm(forms.Form):
    """Filters for the refresh cost report."""

    DATE_FIELD_CHOICES = (
        # First and default: the one that actually binds a refresh. See
        # ModelLifecycle.effective_end_of_life — a model can sit under a
        # support contract for years after its last security fix, and it is
        # the security date that says when the hardware has to be gone.
        ('effective_eol', 'End of life (soonest of support / security)'),
        ('end_of_support', 'End of support'),
        ('end_of_sale', 'End of sale'),
        ('end_of_security_support', 'End of security support'),
        ('end_of_service_attach', 'End of service attach'),
        ('end_of_service_contract_renewal', 'End of contract renewal'),
    )

    date_field = forms.ChoiceField(
        choices=DATE_FIELD_CHOICES, initial='effective_eol', required=False,
        label='Milestone',
    )
    after = forms.DateField(required=False, widget=DatePicker(), label='Between')
    before = forms.DateField(required=False, widget=DatePicker(), label='and')
    manufacturer = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False,
    )
    site = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(), required=False,
        help_text='Only count installed units at these sites',
    )


# --------------------------------------------------------------------------- #
# Software
# --------------------------------------------------------------------------- #

class SoftwareVersionForm(NetBoxModelForm):
    platform = DynamicModelChoiceField(queryset=Platform.objects.all(), selector=True)

    fieldsets = (
        FieldSet('platform', 'version', 'release_date', name='Version'),
        FieldSet(
            'image_filename', 'image_url', 'image_size',
            'checksum_type', 'checksum', name='Image',
        ),
        FieldSet('description', name='Reference'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = SoftwareVersion
        fields = (
            'platform', 'version', 'release_date',
            'image_filename', 'image_url', 'image_size',
            'checksum_type', 'checksum',
            'description', 'comments', 'tags',
        )
        widgets = {'release_date': DatePicker()}
        help_texts = {
            'image_url': 'Direct link, typically the internal image server. '
                         'Plain http is fine — this is a download link, not a page resource.',
        }


class SoftwareVersionFilterForm(NetBoxModelFilterSetForm):
    model = SoftwareVersion
    platform_id = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(), required=False, label='Platform',
    )
    release_date__gte = forms.DateField(
        required=False, widget=DatePicker(), label='Released after',
    )
    release_date__lte = forms.DateField(
        required=False, widget=DatePicker(), label='Released before',
    )
    checksum_type = forms.MultipleChoiceField(choices=ChecksumTypeChoices, required=False)
    has_image = forms.NullBooleanField(required=False, label='Has a downloadable image')
    tag = TagFilterField(model)


class SoftwareVersionImportForm(NetBoxModelImportForm):
    platform = CSVModelChoiceField(
        queryset=Platform.objects.all(), to_field_name='name',
        help_text='Platform name',
    )
    checksum_type = CSVChoiceField(choices=ChecksumTypeChoices, required=False)

    class Meta:
        model = SoftwareVersion
        fields = (
            'platform', 'version', 'release_date',
            'image_filename', 'image_url', 'image_size', 'checksum_type', 'checksum',
            'description', 'comments', 'tags',
        )


class SoftwareVersionBulkEditForm(NetBoxModelBulkEditForm):
    model = SoftwareVersion
    platform = DynamicModelChoiceField(queryset=Platform.objects.all(), required=False)
    release_date = forms.DateField(required=False, widget=DatePicker())
    image_url = forms.URLField(required=False, max_length=500)
    checksum_type = forms.ChoiceField(
        choices=[('', '---------')] + list(ChecksumTypeChoices), required=False,
    )
    description = forms.CharField(required=False, max_length=200)

    nullable_fields = ('release_date', 'image_url', 'checksum_type', 'description')


class SoftwareStandardForm(NetBoxModelForm):
    device_type = DynamicModelChoiceField(
        queryset=DeviceType.objects.all(), required=False, selector=True,
    )
    platform = DynamicModelChoiceField(
        queryset=Platform.objects.all(), required=False, selector=True,
    )
    approved_versions = DynamicModelMultipleChoiceField(
        queryset=SoftwareVersion.objects.all(),
        help_text='Every version that counts as compliant. List them explicitly — '
                  'there is no "at or above" rule.',
    )
    preferred_version = DynamicModelChoiceField(
        queryset=SoftwareVersion.objects.all(), required=False,
        help_text='Which of the approved versions to deploy on new kit',
    )

    fieldsets = (
        FieldSet(
            TabbedGroups(
                FieldSet('device_type', name='Device Type'),
                FieldSet('platform', name='Platform'),
            ),
            name='Applies to',
        ),
        FieldSet('approved_versions', 'preferred_version', name='Approved software'),
        FieldSet('valid_from', 'valid_to', name='In force'),
        FieldSet('description', name='Reference'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = SoftwareStandard
        fields = (
            'approved_versions', 'preferred_version', 'valid_from', 'valid_to',
            'description', 'comments', 'tags',
        )
        widgets = {'valid_from': DatePicker(), 'valid_to': DatePicker()}
        help_texts = {
            'valid_from': 'The date we adopted this standard.',
            'valid_to': 'Leave empty while this standard is current. Set it when '
                        'superseding, so the history stays queryable.',
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.get('initial', {}).copy()
        if instance is not None and instance.assigned_object:
            field = ('device_type' if instance.assigned_object._meta.model_name == 'devicetype'
                     else 'platform')
            initial[field] = instance.assigned_object
        kwargs['initial'] = initial
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        device_type = self.cleaned_data.get('device_type')
        platform = self.cleaned_data.get('platform')
        if device_type and platform:
            raise forms.ValidationError('Select a device type or a platform, not both.')
        target = device_type or platform
        if target is None:
            raise forms.ValidationError('Select what this standard applies to.')
        self.instance.assigned_object = target

        # The model cannot check this on create — the M2M does not exist until
        # the row is saved — so it is enforced here against the submitted data.
        approved = self.cleaned_data.get('approved_versions')
        preferred = self.cleaned_data.get('preferred_version')
        if approved and preferred and preferred not in approved:
            raise forms.ValidationError(
                {'preferred_version': 'The preferred version must be one of the approved versions.'}
            )

        # A platform-scoped standard approving versions of some other OS family
        # is always a mistake, and it would silently never match anything.
        if platform and approved:
            wrong = [v for v in approved if v.platform_id != platform.pk]
            if wrong:
                raise forms.ValidationError({
                    'approved_versions': 'These are not %s versions: %s' % (
                        platform, ', '.join(str(v) for v in wrong)
                    )
                })


class SoftwareStandardFilterForm(NetBoxModelFilterSetForm):
    model = SoftwareStandard
    device_type_id = DynamicModelMultipleChoiceField(
        queryset=DeviceType.objects.all(), required=False, label='Device type',
    )
    platform_id = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(), required=False, label='Platform',
    )
    approved_version_id = DynamicModelMultipleChoiceField(
        queryset=SoftwareVersion.objects.all(), required=False, label='Approves version',
    )
    is_active = forms.NullBooleanField(required=False, label='In force today')
    valid_from__gte = forms.DateField(required=False, widget=DatePicker(), label='Adopted after')
    valid_from__lte = forms.DateField(required=False, widget=DatePicker(), label='Adopted before')
    tag = TagFilterField(model)


class SoftwareStandardBulkEditForm(NetBoxModelBulkEditForm):
    model = SoftwareStandard
    valid_to = forms.DateField(
        required=False, widget=DatePicker(),
        help_text='Close out several standards at once when superseding them',
    )
    description = forms.CharField(required=False, max_length=200)

    nullable_fields = ('valid_to', 'description')


class DeviceSoftwareForm(NetBoxModelForm):
    device = DynamicModelChoiceField(queryset=Device.objects.all(), selector=True)
    software_version = DynamicModelChoiceField(
        queryset=SoftwareVersion.objects.all(), required=False,
        help_text='Leave empty if the running version is genuinely unknown',
    )

    fieldsets = (
        FieldSet('device', 'software_version', 'raw_version', name='Running software'),
        FieldSet('source', 'collected_at', 'last_checked', name='Provenance'),
        FieldSet(
            'exempt', 'exempt_reason', 'exempt_approved_by', 'exempt_approved_on',
            'exempt_review_by', name='Do not upgrade',
        ),
        FieldSet('description', name='Reference'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = DeviceSoftware
        fields = (
            'device', 'software_version', 'raw_version', 'source',
            'collected_at', 'last_checked',
            'exempt', 'exempt_reason', 'exempt_approved_by', 'exempt_approved_on',
            'exempt_review_by', 'description', 'comments', 'tags',
        )
        widgets = {
            'collected_at': DateTimePicker(),
            'last_checked': DateTimePicker(),
            'exempt_approved_on': DatePicker(),
            'exempt_review_by': DatePicker(),
        }


class DeviceSoftwareFilterForm(NetBoxModelFilterSetForm):
    model = DeviceSoftware
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(), required=False, label='Device',
    )
    site_id = DynamicModelMultipleChoiceField(
        queryset=Site.objects.all(), required=False, label='Site',
    )
    platform_id = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(), required=False, label='Platform',
    )
    software_version_id = DynamicModelMultipleChoiceField(
        queryset=SoftwareVersion.objects.all(), required=False, label='Version',
    )
    source = forms.MultipleChoiceField(choices=SoftwareSourceChoices, required=False)
    exempt = forms.NullBooleanField(required=False, label='Do not upgrade')
    has_version = forms.NullBooleanField(required=False, label='Version known')
    is_stale = forms.NullBooleanField(required=False, label='Reading is stale')
    tag = TagFilterField(model)


class DeviceSoftwareImportForm(NetBoxModelImportForm):
    """Bulk import of running versions.

    `platform` and `version` are matched against the version catalogue and the
    version is CREATED if it is not there yet. That is deliberate: the point of
    this importer is initial population from whatever inventory you already
    have, and demanding that every version be catalogued by hand first would
    make it useless for exactly that job.
    """

    device = CSVModelChoiceField(
        queryset=Device.objects.all(), to_field_name='name', help_text='Device name',
    )
    platform = CSVModelChoiceField(
        queryset=Platform.objects.all(), to_field_name='name', required=False,
        help_text='Platform of the running version; defaults to the device platform',
    )
    version = forms.CharField(
        required=False, help_text='Running version, exactly as the device reports it',
    )
    source = CSVChoiceField(choices=SoftwareSourceChoices, required=False)

    class Meta:
        model = DeviceSoftware
        fields = (
            'device', 'platform', 'version', 'source', 'collected_at',
            'exempt', 'exempt_reason', 'exempt_approved_by', 'exempt_approved_on',
            'exempt_review_by', 'description', 'comments', 'tags',
        )

    def clean(self):
        super().clean()
        device = self.cleaned_data.get('device')
        version = (self.cleaned_data.get('version') or '').strip()
        platform = self.cleaned_data.get('platform') or (device.platform if device else None)

        if not version:
            return  # a row that only sets an exemption is legitimate

        if platform is None:
            raise forms.ValidationError({
                'platform': 'Give a platform, or set one on the device — a version '
                            'string means nothing without knowing the OS family.'
            })

        software_version, _created = SoftwareVersion.objects.get_or_create(
            platform=platform, version=version,
        )
        self.instance.software_version = software_version
        self.instance.raw_version = version
        if not self.cleaned_data.get('source'):
            self.instance.source = SoftwareSourceChoices.SOURCE_IMPORT


class DeviceSoftwareBulkEditForm(NetBoxModelBulkEditForm):
    model = DeviceSoftware
    software_version = DynamicModelChoiceField(
        queryset=SoftwareVersion.objects.all(), required=False,
    )
    source = forms.ChoiceField(
        choices=[('', '---------')] + list(SoftwareSourceChoices), required=False,
    )
    exempt = forms.NullBooleanField(required=False, label='Do not upgrade')
    exempt_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    exempt_approved_by = forms.CharField(required=False, max_length=100)
    exempt_review_by = forms.DateField(required=False, widget=DatePicker())
    description = forms.CharField(required=False, max_length=200)

    nullable_fields = (
        'software_version', 'exempt_reason', 'exempt_approved_by', 'exempt_review_by',
        'description',
    )


class ComplianceReportForm(forms.Form):
    """Filters for the software compliance report."""

    as_of = forms.DateField(
        required=False, widget=DatePicker(), label='Standard as of',
        help_text='Which standard was in force on this date. The running versions '
                  'shown are always the current ones — we do not snapshot those.',
    )
    site = DynamicModelMultipleChoiceField(queryset=Site.objects.all(), required=False)
    role = DynamicModelMultipleChoiceField(
        queryset=DeviceRole.objects.all(), required=False, label='Device role',
    )
    platform = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(), required=False,
    )
    manufacturer = DynamicModelMultipleChoiceField(
        queryset=Manufacturer.objects.all(), required=False,
    )
    device_type = DynamicModelMultipleChoiceField(
        queryset=DeviceType.objects.all(), required=False,
    )
    status = forms.MultipleChoiceField(
        choices=ComplianceStatusChoices, required=False,
        help_text='Leave empty to show every state, including exempt devices',
    )
