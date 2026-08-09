from netbox.search import SearchIndex, register_search

from netbox_discovery.models import DiscoveryPoller, OnboardingRequest


@register_search
class OnboardingRequestIndex(SearchIndex):
    model = OnboardingRequest
    fields = (
        ('address', 100),
        ('description', 500),
        ('error', 1000),
    )
    display_attrs = ('status', 'site', 'poller')


@register_search
class DiscoveryPollerIndex(SearchIndex):
    model = DiscoveryPoller
    fields = (
        ('name', 100),
        ('description', 500),
    )
    display_attrs = ('last_seen_at',)
