import django_filters
from dcim.models import DeviceType, Manufacturer, ModuleType
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

from netbox_refresh.choices import LifecycleSourceChoices
from netbox_refresh.models import ModelLifecycle

__all__ = ('ModelLifecycleFilterSet',)


class ModelLifecycleFilterSet(NetBoxModelFilterSet):
    assigned_object_type_id = django_filters.ModelMultipleChoiceFilter(
        queryset=ContentType.objects.all()
    )
    manufacturer_id = django_filters.ModelMultipleChoiceFilter(
        queryset=Manufacturer.objects.all(), method='filter_manufacturer',
        label='Manufacturer',
    )
    source = django_filters.MultipleChoiceFilter(choices=LifecycleSourceChoices)
    replacement_device_type_id = django_filters.ModelMultipleChoiceFilter(
        queryset=DeviceType.objects.all(), label='Replacement device type',
    )
    replacement_module_type_id = django_filters.ModelMultipleChoiceFilter(
        queryset=ModuleType.objects.all(), label='Replacement module type',
    )
    has_replacement = django_filters.BooleanFilter(
        method='filter_has_replacement', label='Has a replacement model',
    )
    has_cost = django_filters.BooleanFilter(
        method='filter_has_cost', label='Has a replacement cost',
    )

    class Meta:
        model = ModelLifecycle
        # Date ranges drive the refresh report, so every lifecycle date gets
        # gte/lte lookups (end_of_support__gte=..., end_of_support__lte=...).
        fields = {
            'id': ['exact'],
            'announcement_date': ['exact', 'gte', 'lte'],
            'end_of_sale': ['exact', 'gte', 'lte'],
            'end_of_sw_maintenance': ['exact', 'gte', 'lte'],
            'end_of_security_support': ['exact', 'gte', 'lte'],
            'end_of_service_attach': ['exact', 'gte', 'lte'],
            'end_of_service_contract_renewal': ['exact', 'gte', 'lte'],
            'end_of_support': ['exact', 'gte', 'lte'],
            'replacement_cost': ['exact', 'gte', 'lte'],
            'currency': ['exact'],
            'bulletin_number': ['exact', 'icontains'],
        }

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        device_types = DeviceType.objects.filter(
            Q(model__icontains=value) | Q(part_number__icontains=value)
        ).values_list('pk', flat=True)
        module_types = ModuleType.objects.filter(
            Q(model__icontains=value) | Q(part_number__icontains=value)
        ).values_list('pk', flat=True)
        return queryset.filter(
            Q(bulletin_number__icontains=value)
            | Q(description__icontains=value)
            | Q(replacement_notes__icontains=value)
            | Q(assigned_object_type__model='devicetype', assigned_object_id__in=list(device_types))
            | Q(assigned_object_type__model='moduletype', assigned_object_id__in=list(module_types))
        ).distinct()

    def filter_manufacturer(self, queryset, name, value):
        if not value:
            return queryset
        device_types = DeviceType.objects.filter(manufacturer__in=value).values_list('pk', flat=True)
        module_types = ModuleType.objects.filter(manufacturer__in=value).values_list('pk', flat=True)
        return queryset.filter(
            Q(assigned_object_type__model='devicetype', assigned_object_id__in=list(device_types))
            | Q(assigned_object_type__model='moduletype', assigned_object_id__in=list(module_types))
        )

    def filter_has_replacement(self, queryset, name, value):
        query = Q(replacement_device_type__isnull=False) | Q(replacement_module_type__isnull=False)
        return queryset.filter(query) if value else queryset.exclude(query)

    def filter_has_cost(self, queryset, name, value):
        if value:
            return queryset.filter(replacement_cost__isnull=False)
        return queryset.filter(replacement_cost__isnull=True)
