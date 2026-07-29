import django_filters
from dcim.models import Device
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

from netbox_quotes.choices import MatchStateChoices, QuoteStatusChoices
from netbox_quotes.models import Quote, QuoteLine, Vendor

__all__ = ('VendorFilterSet', 'QuoteFilterSet', 'QuoteLineFilterSet')


class VendorFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = Vendor
        fields = ('id', 'name')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) | Q(description__icontains=value)
        )


class QuoteFilterSet(NetBoxModelFilterSet):
    vendor_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Vendor.objects.all(), label='Vendor (ID)'
    )
    vendor = django_filters.ModelMultipleChoiceFilter(
        field_name='vendor__name',
        queryset=Vendor.objects.all(),
        to_field_name='name',
        label='Vendor (name)',
    )
    status = django_filters.MultipleChoiceFilter(choices=QuoteStatusChoices)

    class Meta:
        model = Quote
        fields = ('id', 'number', 'currency', 'quote_date', 'valid_until')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(number__icontains=value)
            | Q(description__icontains=value)
            | Q(vendor__name__icontains=value)
        )


class QuoteLineFilterSet(NetBoxModelFilterSet):
    quote_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Quote.objects.all(), label='Quote (ID)'
    )
    vendor_id = django_filters.ModelMultipleChoiceFilter(
        field_name='quote__vendor',
        queryset=Vendor.objects.all(),
        label='Vendor (ID)',
    )
    match_state = django_filters.MultipleChoiceFilter(choices=MatchStateChoices)
    assigned_object_type_id = django_filters.ModelMultipleChoiceFilter(
        queryset=ContentType.objects.all()
    )
    device_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Device.objects.all(),
        method='filter_device',
        label='Covered device (ID)',
    )

    class Meta:
        model = QuoteLine
        fields = (
            'id',
            'serial',
            'part_number',
            'service_sku',
            'assigned_object_id',
            'coverage_start',
            'coverage_end',
        )

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(serial__icontains=value)
            | Q(part_number__icontains=value)
            | Q(service_sku__icontains=value)
            | Q(description__icontains=value)
            | Q(quote__number__icontains=value)
        )

    def filter_device(self, queryset, name, value):
        """Rollup filter: lines covering the device directly or via its components."""
        if not value:
            return queryset
        return queryset.for_devices([d.pk for d in value])
