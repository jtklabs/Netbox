from dcim.models import DeviceType, Manufacturer, ModuleType, Site
from django import forms
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
from utilities.forms.widgets import DatePicker

from netbox_refresh.choices import LifecycleSourceChoices
from netbox_refresh.models import ModelLifecycle

__all__ = (
    'ModelLifecycleForm',
    'ModelLifecycleFilterForm',
    'ModelLifecycleImportForm',
    'ModelLifecycleBulkEditForm',
    'RefreshReportForm',
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
        ('end_of_support', 'End of support'),
        ('end_of_sale', 'End of sale'),
        ('end_of_security_support', 'End of security support'),
        ('end_of_service_attach', 'End of service attach'),
        ('end_of_service_contract_renewal', 'End of contract renewal'),
    )

    date_field = forms.ChoiceField(
        choices=DATE_FIELD_CHOICES, initial='end_of_support', required=False,
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
