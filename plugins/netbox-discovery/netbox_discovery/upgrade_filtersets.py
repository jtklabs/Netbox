import django_filters
from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet
from dcim.models import Device
from .models import PrestagePolicy, UpgradeDependency, UpgradeGroup, UpgradeJob


class UpgradeJobFilterSet(NetBoxModelFilterSet):
    site_id = django_filters.NumberFilter(field_name='device__site_id')
    role_id = django_filters.NumberFilter(field_name='device__role_id')
    device_type_id = django_filters.NumberFilter(field_name='device__device_type_id')
    poller = django_filters.CharFilter(field_name='poller__name')

    class Meta:
        model = UpgradeJob
        fields = ('id', 'batch_id', 'device_id', 'poller_id', 'status', 'operation', 'run_id')

    def search(self, queryset, name, value):
        return queryset.filter(Q(device_name__icontains=value) | Q(description__icontains=value))


class UpgradeGroupFilterSet(NetBoxModelFilterSet):
    member_id = django_filters.ModelMultipleChoiceFilter(field_name='members', queryset=Device.objects.all())
    depends_on_id = django_filters.ModelMultipleChoiceFilter(field_name='depends_on', queryset=UpgradeGroup.objects.all())

    class Meta:
        model = UpgradeGroup
        fields = ('id', 'name', 'source', 'stale', 'max_concurrent', 'key')

    def search(self, queryset, name, value):
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))


class UpgradeDependencyFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = UpgradeDependency
        fields = ('id', 'upstream_id', 'downstream_id', 'source', 'stale', 'key')

    def search(self, queryset, name, value):
        return queryset.filter(Q(upstream__name__icontains=value) | Q(downstream__name__icontains=value))


class PrestagePolicyFilterSet(NetBoxModelFilterSet):
    manufacturer_id = django_filters.NumberFilter(field_name='device_type__manufacturer_id')

    class Meta:
        model = PrestagePolicy
        fields = ('id', 'device_type_id', 'enabled', 'interval_hours', 'window_hours')

    def search(self, queryset, name, value):
        return queryset.filter(Q(device_type__model__icontains=value) | Q(description__icontains=value))
