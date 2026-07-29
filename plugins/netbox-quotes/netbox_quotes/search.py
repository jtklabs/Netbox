from netbox.search import SearchIndex, register_search

from netbox_quotes.models import Quote, QuoteLine, Vendor


@register_search
class VendorIndex(SearchIndex):
    model = Vendor
    fields = (
        ('name', 100),
        ('description', 4000),
        ('comments', 5000),
    )
    display_attrs = ('description',)


@register_search
class QuoteIndex(SearchIndex):
    model = Quote
    fields = (
        ('number', 100),
        ('description', 4000),
        ('comments', 5000),
    )
    display_attrs = ('vendor', 'status', 'quote_date')


@register_search
class QuoteLineIndex(SearchIndex):
    model = QuoteLine
    fields = (
        ('serial', 60),
        ('part_number', 100),
        ('service_sku', 110),
        ('description', 4000),
        ('comments', 5000),
    )
    display_attrs = ('quote', 'serial', 'match_state')
