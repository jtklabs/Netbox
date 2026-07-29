import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from netbox_quotes.models import Quote, QuoteLine, Vendor

__all__ = ('VendorTable', 'QuoteTable', 'QuoteLineTable')


class VendorTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = Vendor
        fields = ('pk', 'name', 'portal_url', 'description')
        default_columns = ('name', 'portal_url', 'description')


class QuoteTable(NetBoxTable):
    number = tables.Column(linkify=True)
    vendor = tables.Column(linkify=True)
    status = columns.ChoiceFieldColumn()
    total = tables.Column(orderable=False, verbose_name='Total')

    class Meta(NetBoxTable.Meta):
        model = Quote
        fields = (
            'pk',
            'number',
            'vendor',
            'status',
            'quote_date',
            'valid_until',
            'currency',
            'total',
            'description',
        )
        default_columns = (
            'number',
            'vendor',
            'status',
            'quote_date',
            'valid_until',
            'total',
        )


class QuoteLineTable(NetBoxTable):
    quote = tables.Column(linkify=True)
    description = tables.Column(linkify=True)
    serial = tables.Column()
    assigned_object = tables.Column(linkify=True, orderable=False, verbose_name='Assigned to')
    device = tables.Column(
        linkify=True, orderable=False, accessor='device', verbose_name='Device'
    )
    match_state = columns.ChoiceFieldColumn()
    effective_total = tables.Column(orderable=False, verbose_name='Total')

    class Meta(NetBoxTable.Meta):
        model = QuoteLine
        fields = (
            'pk',
            'quote',
            'line_number',
            'description',
            'part_number',
            'service_sku',
            'serial',
            'quantity',
            'unit_price',
            'effective_total',
            'coverage_start',
            'coverage_end',
            'assigned_object',
            'device',
            'match_state',
        )
        default_columns = (
            'quote',
            'description',
            'serial',
            'service_sku',
            'effective_total',
            'coverage_end',
            'assigned_object',
            'match_state',
        )
