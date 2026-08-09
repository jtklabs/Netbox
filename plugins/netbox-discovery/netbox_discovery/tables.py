import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from netbox_discovery.models import DiscoveryPoller, OnboardingRequest

__all__ = ('DiscoveryPollerTable', 'OnboardingRequestTable')


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
    is_stale = columns.BooleanColumn(verbose_name='Stale')
    open_requests = tables.Column(
        accessor='requests__count', orderable=False, verbose_name='Open requests'
    )
    tags = columns.TagColumn(url_name='plugins:netbox_discovery:discoverypoller_list')

    class Meta(NetBoxTable.Meta):
        model = DiscoveryPoller
        fields = (
            'pk', 'id', 'name', 'tenant', 'last_seen_at', 'is_stale', 'version',
            'last_scan_summary', 'open_requests', 'description', 'tags',
        )
        default_columns = ('name', 'last_seen_at', 'is_stale', 'version', 'last_scan_summary')
