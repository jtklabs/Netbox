from netbox.search import SearchIndex, register_search

from netbox_discovery.models import (
    DiscoveryPoller,
    DiscoveryRule,
    HardwareReplacement,
    OnboardingRequest,
    StrippedDomain,
)


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


@register_search
class HardwareReplacementIndex(SearchIndex):
    model = HardwareReplacement
    fields = (
        ('old_serial', 100),
        ('new_serial', 100),
        ('model_name', 500),
        ('description', 500),
    )
    display_attrs = ('kind', 'device', 'detected_at')


@register_search
class DiscoveryRuleIndex(SearchIndex):
    model = DiscoveryRule
    fields = (
        ('name', 100),
        ('match_value', 500),
        ('set_value', 500),
        ('description', 500),
    )
    display_attrs = ('enabled', 'set_field', 'set_value')


@register_search
class StrippedDomainIndex(SearchIndex):
    model = StrippedDomain
    fields = (
        ('domain', 100),
        ('description', 500),
    )
    display_attrs = ('enabled',)
