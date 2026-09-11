import django_filters
from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet
from .models import UpgradeJob


class UpgradeJobFilterSet(NetBoxModelFilterSet):
    site_id = django_filters.NumberFilter(field_name='device__site_id')
    role_id = django_filters.NumberFilter(field_name='device__role_id')
    poller = django_filters.CharFilter(field_name='poller__name')

    class Meta:
        model = UpgradeJob
        fields = ('id', 'batch_id', 'device_id', 'poller_id', 'status', 'operation', 'run_id')

    def search(self, queryset, name, value):
        return queryset.filter(Q(device_name__icontains=value) | Q(description__icontains=value))
