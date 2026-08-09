import django_filters
from django.db.models import Q
from netbox.filtersets import NetBoxModelFilterSet

from netbox_discovery.choices import OnboardingStatusChoices
from netbox_discovery.models import DiscoveryPoller, OnboardingRequest

__all__ = ('DiscoveryPollerFilterSet', 'OnboardingRequestFilterSet')


class DiscoveryPollerFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = DiscoveryPoller
        fields = ('id', 'name', 'version', 'tenant_id')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value) | Q(description__icontains=value)
        )


class OnboardingRequestFilterSet(NetBoxModelFilterSet):
    status = django_filters.MultipleChoiceFilter(
        choices=OnboardingStatusChoices, null_value=None
    )
    poller_id = django_filters.ModelMultipleChoiceFilter(
        queryset=DiscoveryPoller.objects.all(), label='Poller (ID)'
    )
    poller = django_filters.ModelMultipleChoiceFilter(
        field_name='poller__name', to_field_name='name',
        queryset=DiscoveryPoller.objects.all(), label='Poller (name)',
    )
    needs_attention = django_filters.BooleanFilter(
        method='filter_needs_attention',
        label='Waiting on a person',
    )

    class Meta:
        model = OnboardingRequest
        fields = ('id', 'address', 'status', 'site_id', 'device_id',
                  'tenant_id', 'vrf_id', 'used_default_region')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(address__icontains=value)
            | Q(description__icontains=value)
            | Q(error__icontains=value)
        )

    def filter_needs_attention(self, queryset, name, value):
        """Requests where the next move is ours, not a poller's."""
        if value is None:
            return queryset
        lookup = queryset.filter(status__in=OnboardingStatusChoices.NEEDS_ATTENTION)
        return lookup if value else queryset.exclude(
            status__in=OnboardingStatusChoices.NEEDS_ATTENTION
        )
