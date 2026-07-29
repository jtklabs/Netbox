from netbox.api.viewsets import NetBoxModelViewSet

from netbox_quotes import filtersets
from netbox_quotes.api.serializers import (
    QuoteLineSerializer,
    QuoteSerializer,
    VendorSerializer,
)
from netbox_quotes.models import Quote, QuoteLine, Vendor

__all__ = ('VendorViewSet', 'QuoteViewSet', 'QuoteLineViewSet')


class VendorViewSet(NetBoxModelViewSet):
    queryset = Vendor.objects.all()
    serializer_class = VendorSerializer
    filterset_class = filtersets.VendorFilterSet


class QuoteViewSet(NetBoxModelViewSet):
    queryset = Quote.objects.prefetch_related('vendor', 'lines')
    serializer_class = QuoteSerializer
    filterset_class = filtersets.QuoteFilterSet


class QuoteLineViewSet(NetBoxModelViewSet):
    queryset = QuoteLine.objects.prefetch_related(
        'quote__vendor', 'assigned_object'
    )
    serializer_class = QuoteLineSerializer
    filterset_class = filtersets.QuoteLineFilterSet
