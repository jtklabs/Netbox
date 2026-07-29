from dcim.models import Device, InventoryItem, Module
from django import forms
from netbox.forms import (
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

from netbox_quotes.choices import MatchStateChoices, QuoteStatusChoices
from netbox_quotes.models import Quote, QuoteLine, Vendor

__all__ = (
    'VendorForm',
    'VendorFilterForm',
    'VendorImportForm',
    'QuoteForm',
    'QuoteFilterForm',
    'QuoteImportForm',
    'QuoteLineForm',
    'QuoteLineFilterForm',
    'QuoteLineImportForm',
)


class VendorForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'portal_url', 'description', name='Vendor'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = Vendor
        fields = ('name', 'portal_url', 'description', 'comments', 'tags')


class VendorFilterForm(NetBoxModelFilterSetForm):
    model = Vendor
    tag = TagFilterField(model)


class VendorImportForm(NetBoxModelImportForm):
    class Meta:
        model = Vendor
        fields = ('name', 'portal_url', 'description', 'comments', 'tags')


class QuoteForm(NetBoxModelForm):
    vendor = DynamicModelChoiceField(queryset=Vendor.objects.all())

    fieldsets = (
        FieldSet(
            'vendor',
            'number',
            'status',
            'quote_date',
            'valid_until',
            'currency',
            'document',
            'description',
            name='Quote',
        ),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = Quote
        fields = (
            'vendor',
            'number',
            'status',
            'quote_date',
            'valid_until',
            'currency',
            'document',
            'description',
            'comments',
            'tags',
        )
        widgets = {
            'quote_date': DatePicker(),
            'valid_until': DatePicker(),
        }


class QuoteFilterForm(NetBoxModelFilterSetForm):
    model = Quote
    vendor_id = DynamicModelMultipleChoiceField(
        queryset=Vendor.objects.all(), required=False, label='Vendor'
    )
    status = forms.MultipleChoiceField(choices=QuoteStatusChoices, required=False)
    tag = TagFilterField(model)


class QuoteImportForm(NetBoxModelImportForm):
    vendor = CSVModelChoiceField(
        queryset=Vendor.objects.all(),
        to_field_name='name',
        help_text='Vendor name',
    )
    status = CSVChoiceField(
        choices=QuoteStatusChoices, required=False, help_text='Quote status'
    )

    class Meta:
        model = Quote
        fields = (
            'vendor',
            'number',
            'status',
            'quote_date',
            'valid_until',
            'currency',
            'description',
            'comments',
            'tags',
        )


class QuoteLineForm(NetBoxModelForm):
    quote = DynamicModelChoiceField(queryset=Quote.objects.all())
    device = DynamicModelChoiceField(
        queryset=Device.objects.all(), required=False, selector=True
    )
    module = DynamicModelChoiceField(
        queryset=Module.objects.all(), required=False, selector=True
    )
    inventory_item = DynamicModelChoiceField(
        queryset=InventoryItem.objects.all(),
        required=False,
        selector=True,
        label='Inventory item',
    )

    fieldsets = (
        FieldSet(
            'quote',
            'line_number',
            'description',
            'part_number',
            'service_sku',
            'serial',
            name='Line',
        ),
        FieldSet(
            'quantity',
            'unit_price',
            'line_total',
            'coverage_start',
            'coverage_end',
            name='Pricing & coverage',
        ),
        FieldSet(
            TabbedGroups(
                FieldSet('device', name='Device'),
                FieldSet('module', name='Module'),
                FieldSet('inventory_item', name='Inventory Item'),
            ),
            name='Assignment (leave empty to auto-match by serial)',
        ),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = QuoteLine
        fields = (
            'quote',
            'line_number',
            'description',
            'part_number',
            'service_sku',
            'serial',
            'quantity',
            'unit_price',
            'line_total',
            'coverage_start',
            'coverage_end',
            'comments',
            'tags',
        )
        widgets = {
            'coverage_start': DatePicker(),
            'coverage_end': DatePicker(),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        initial = kwargs.get('initial', {}).copy()
        if instance is not None and instance.assigned_object:
            model_name = instance.assigned_object._meta.model_name
            field = {
                'device': 'device',
                'module': 'module',
                'inventoryitem': 'inventory_item',
            }[model_name]
            initial[field] = instance.assigned_object
        kwargs['initial'] = initial
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        selected = [
            obj
            for obj in (
                self.cleaned_data.get('device'),
                self.cleaned_data.get('module'),
                self.cleaned_data.get('inventory_item'),
            )
            if obj is not None
        ]
        if len(selected) > 1:
            raise forms.ValidationError(
                'A line can be assigned to only one object (device, module, or inventory item).'
            )
        assigned = selected[0] if selected else None
        if assigned is not None and assigned != self.instance.assigned_object:
            self.instance.assigned_object = assigned
            self.instance.match_state = MatchStateChoices.STATE_MANUAL
        elif assigned is None and self.instance.assigned_object is not None:
            # Assignment explicitly cleared; model save() re-matches by serial.
            self.instance.assigned_object = None
            self.instance.match_state = MatchStateChoices.STATE_UNMATCHED


class QuoteLineFilterForm(NetBoxModelFilterSetForm):
    model = QuoteLine
    vendor_id = DynamicModelMultipleChoiceField(
        queryset=Vendor.objects.all(), required=False, label='Vendor'
    )
    quote_id = DynamicModelMultipleChoiceField(
        queryset=Quote.objects.all(), required=False, label='Quote'
    )
    device_id = DynamicModelMultipleChoiceField(
        queryset=Device.objects.all(), required=False, label='Covered device'
    )
    match_state = forms.MultipleChoiceField(choices=MatchStateChoices, required=False)
    serial = forms.CharField(required=False)
    tag = TagFilterField(model)


class QuoteLineImportForm(NetBoxModelImportForm):
    quote = CSVModelChoiceField(
        queryset=Quote.objects.all(),
        to_field_name='number',
        help_text='Quote number (must be unique across vendors for import)',
    )

    class Meta:
        model = QuoteLine
        fields = (
            'quote',
            'line_number',
            'description',
            'part_number',
            'service_sku',
            'serial',
            'quantity',
            'unit_price',
            'line_total',
            'coverage_start',
            'coverage_end',
            'comments',
            'tags',
        )
