from netbox.search import SearchIndex, register_search

from netbox_compliance.models import ConfigCompliance, ConfigStandard


@register_search
class ConfigStandardIndex(SearchIndex):
    model = ConfigStandard
    fields = (
        ('name', 100),
        ('match_pattern', 500),
        ('remediation_notes', 2000),
        ('description', 4000),
        ('comments', 5000),
    )
    display_attrs = ('check_type', 'valid_from', 'valid_to')


@register_search
class ConfigComplianceIndex(SearchIndex):
    model = ConfigCompliance
    fields = (
        # `observed` is indexed so somebody can search for the offending line
        # they were told about. It is redacted before it ever reaches NetBox,
        # so this indexes evidence, not secrets.
        ('observed', 500),
        ('error_message', 1000),
        ('exempt_reason', 2000),
        ('description', 4000),
        ('comments', 5000),
    )
    display_attrs = ('device', 'standard', 'result')
