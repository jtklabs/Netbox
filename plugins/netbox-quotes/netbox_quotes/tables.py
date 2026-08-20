import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from netbox_quotes.models import Quote, QuoteLine, Vendor

__all__ = (
    'VendorTable',
    'QuoteTable',
    'QuoteLineTable',
    'CoverageExpiryTable',
    'EolTransitionTable',
)


class VendorTable(NetBoxTable):
    name = tables.Column(linkify=True)
    is_third_party_maintenance = columns.BooleanColumn(
        verbose_name='Third-party maintenance',
    )

    class Meta(NetBoxTable.Meta):
        model = Vendor
        fields = ('pk', 'name', 'portal_url', 'is_third_party_maintenance', 'description')
        default_columns = ('name', 'portal_url', 'is_third_party_maintenance', 'description')


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


# --------------------------------------------------------------------------- #
# Report tables — rows are plain dicts assembled by the views, not instances
# --------------------------------------------------------------------------- #

class CoverageExpiryTable(tables.Table):
    device = tables.Column(linkify=lambda record: record['device_url'])
    site = tables.Column()
    device_type = tables.Column(verbose_name='Model')
    serial = tables.Column()
    vendor = tables.Column(linkify=lambda record: record['vendor_url'])
    quote = tables.Column(linkify=lambda record: record['quote_url'])
    coverage_end = tables.DateColumn(verbose_name='Coverage ends')
    days_left = tables.TemplateColumn(
        verbose_name='Days left',
        template_code='{% if record.days_left < 0 %}'
                      '<span class="text-danger">expired {{ record.days_overdue }}d ago</span>'
                      '{% else %}{{ record.days_left }}{% endif %}',
    )

    class Meta:
        attrs = {'class': 'table table-hover object-list'}
        empty_text = 'No coverage ends in the selected window.'
        orderable = False


class EolTransitionTable(tables.Table):
    device = tables.Column(linkify=lambda record: record['device_url'])
    site = tables.Column()
    device_type = tables.Column(verbose_name='Model')
    serial = tables.Column()
    eol_date = tables.DateColumn(verbose_name='Mfg end of life')
    vendor = tables.Column(
        linkify=lambda record: record['vendor_url'], verbose_name='Covered by',
    )
    coverage_end = tables.DateColumn(verbose_name='Coverage ends')
    state = tables.TemplateColumn(
        verbose_name='Action',
        template_code='{% if record.state == "uncovered" %}'
                      '<span class="badge text-bg-red">Needs coverage now</span>'
                      '{% elif record.state == "transition" %}'
                      '<span class="badge text-bg-yellow">Move at renewal</span>'
                      '{% else %}'
                      '<span class="badge text-bg-green">On third-party</span>'
                      '{% endif %}',
    )

    class Meta:
        attrs = {'class': 'table table-hover object-list'}
        empty_text = 'No devices reach end of life in the selected window.'
        orderable = False
