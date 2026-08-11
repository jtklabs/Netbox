import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from netbox_discovery.models import (
    DiscoveryIssue,
    DiscoveryPoller,
    HardwareReplacement,
    OnboardingRequest,
)

__all__ = (
    'DiscoveryIssueTable',
    'DiscoveryPollerTable',
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
