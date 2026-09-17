import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from netbox_discovery.models import (
    DiscoveryIssue,
    DiscoveryPoller,
    DiscoveryRule,
    HardwareReplacement,
    OnboardingRequest,
    StrippedDomain,
)

__all__ = (
    'StrippedDomainTable',
    'DiscoveryIssueTable',
    'DiscoveryPollerTable',
    'DiscoveryRuleTable',
    'HardwareReplacementTable',
    'OnboardingRequestTable',
)


class OnboardingRequestTable(NetBoxTable):
    address = tables.Column(linkify=True, verbose_name='IP address')
    status = columns.ChoiceFieldColumn()
    site = tables.Column(linkify=True)
    tenant = tables.Column(linkify=True)
    poller = tables.Column(linkify=True)
    device = tables.Column(linkify=True, verbose_name='Created device')
    # The column that answers "why has nothing happened?" without opening the
    # request — which is the question the list page mostly gets asked.
    waiting_on = tables.Column(
        accessor='waiting_on', orderable=False, verbose_name='Waiting on'
    )
    discovered_model = tables.Column(
        accessor='discovered_model', orderable=False, verbose_name='Model'
    )
    discovered_serial = tables.Column(
        accessor='discovered_serial', orderable=False, verbose_name='Serial'
    )
    tags = columns.TagColumn(url_name='plugins:netbox_discovery:onboardingrequest_list')

    class Meta(NetBoxTable.Meta):
        model = OnboardingRequest
        fields = (
            'pk', 'id', 'address', 'status', 'waiting_on', 'site', 'tenant', 'poller',
            'discovered_model', 'discovered_serial', 'device', 'requested_by',
            'created', 'scanned_at', 'applied_at', 'description', 'tags',
        )
        default_columns = (
            'address', 'status', 'waiting_on', 'site', 'tenant', 'poller',
            'discovered_model', 'device',
        )


class DiscoveryPollerTable(NetBoxTable):
    name = tables.Column(linkify=True)
    tenant = tables.Column(linkify=True)
    last_seen_at = columns.DateTimeColumn(verbose_name='Last check-in')
    # Asked positively: a BooleanColumn draws false as a red cross, so a column
    # headed "Stale" put a red cross against every healthy poller.
    is_checking_in = columns.BooleanColumn(
        verbose_name='Checking in', orderable=False,
    )
    open_requests = tables.Column(
        accessor='requests__count', orderable=False, verbose_name='Open requests'
    )
    tags = columns.TagColumn(url_name='plugins:netbox_discovery:discoverypoller_list')

    class Meta(NetBoxTable.Meta):
        model = DiscoveryPoller
        fields = (
            'pk', 'id', 'name', 'tenant', 'last_seen_at', 'is_checking_in', 'version',
            'last_scan_summary', 'open_requests', 'description', 'tags',
        )
        default_columns = ('name', 'last_seen_at', 'is_checking_in', 'version',
                       'last_scan_summary')


class HardwareReplacementTable(NetBoxTable):
    # No edit action: replacements are audit rows, immutable by design, and no
    # _edit route exists. ActionsColumn defaults to ('edit', 'delete',
    # 'changelog') and REVERSES each action's URL per row — so with the default
    # the page renders fine while empty and raises NoReverseMatch the moment
    # the first real replacement lands, which is exactly how it reached
    # production unseen.
    actions = columns.ActionsColumn(actions=('delete', 'changelog'))
    device = tables.Column(linkify=True)
    kind = columns.ChoiceFieldColumn()
    old_serial = tables.Column(verbose_name='Serial removed')
    new_serial = tables.Column(verbose_name='Serial fitted')
    replaced_device = tables.Column(linkify=True, verbose_name='Retired record')
    detected_at = columns.DateTimeColumn()
    tags = columns.TagColumn(url_name='plugins:netbox_discovery:hardwarereplacement_list')

    class Meta(NetBoxTable.Meta):
        model = HardwareReplacement
        fields = (
            'pk', 'id', 'detected_at', 'kind', 'device', 'module_bay',
            'old_serial', 'new_serial', 'model_name', 'replaced_device',
            'poller', 'description', 'tags',
        )
        default_columns = (
            'detected_at', 'kind', 'device', 'module_bay',
            'old_serial', 'new_serial', 'model_name',
        )


class DiscoveryIssueTable(NetBoxTable):
    address = tables.Column(linkify=True, verbose_name='Scanned address')
    kind = columns.ChoiceFieldColumn()
    status = columns.ChoiceFieldColumn()
    device = tables.Column(linkify=True, verbose_name='Collided with')
    detected_at = columns.DateTimeColumn()
    tags = columns.TagColumn(url_name='plugins:netbox_discovery:discoveryissue_list')

    class Meta(NetBoxTable.Meta):
        model = DiscoveryIssue
        fields = (
            'pk', 'id', 'detected_at', 'last_seen_at', 'status', 'kind', 'address',
            'reported_name', 'serial', 'device', 'detail', 'poller', 'tags',
        )
        default_columns = (
            'detected_at', 'status', 'kind', 'address', 'reported_name',
            'serial', 'device',
        )


class DiscoveryRuleTable(NetBoxTable):
    name = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()
    # The rule as a sentence is what somebody scanning the list wants; the
    # parts are there for anyone sorting or filtering on one of them.
    sentence = tables.Column(accessor='sentence', orderable=False, verbose_name='Rule')
    # Shown by label, sorted by value: without an explicit order_by a column
    # whose accessor is a method would try to order the queryset by it.
    match_field = tables.Column(
        accessor='get_match_field_display', order_by='match_field',
        verbose_name='When this field',
    )
    match_operator = tables.Column(
        accessor='get_match_operator_display', order_by='match_operator',
        verbose_name='Comparison',
    )
    match_value = tables.Column(verbose_name='This value')
    set_field = tables.Column(
        accessor='get_set_field_display', order_by='set_field', verbose_name='Sets',
    )
    set_value = tables.Column(verbose_name='To')
    only_if_blank = columns.BooleanColumn(verbose_name='Only when empty')
    tags = columns.TagColumn(url_name='plugins:netbox_discovery:discoveryrule_list')

    class Meta(NetBoxTable.Meta):
        model = DiscoveryRule
        fields = (
            'pk', 'id', 'name', 'enabled', 'weight', 'sentence', 'match_field',
            'match_operator', 'match_value', 'set_field', 'set_value',
            'only_if_blank', 'description', 'tags', 'created', 'last_updated',
        )
        default_columns = ('name', 'enabled', 'weight', 'sentence', 'description')


class StrippedDomainTable(NetBoxTable):
    domain = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()
    example = tables.Column(accessor='example', orderable=False, verbose_name='Effect')
    tags = columns.TagColumn(url_name='plugins:netbox_discovery:strippeddomain_list')

    class Meta(NetBoxTable.Meta):
        model = StrippedDomain
        fields = ('pk', 'id', 'domain', 'enabled', 'example', 'description', 'tags',
                  'created', 'last_updated')
        default_columns = ('domain', 'enabled', 'example', 'description')
