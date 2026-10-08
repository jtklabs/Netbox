from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet
from .models import JobProfile, DeviceTypeProfile


class JobProfileFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = JobProfile
        fields = ('id', 'name', 'kind')

    def search(self, queryset, name, value):
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))


class DeviceTypeProfileFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = DeviceTypeProfile
        fields = ('id', 'device_type_id', 'upgrade_profile_id', 'remediation_profile_id')

    def search(self, queryset, name, value):
        return queryset.filter(Q(device_type__model__icontains=value) | Q(description__icontains=value))
