"""GraphQL filters for the quotes models.

Mirrors the REST FilterSets closely enough that a caller can move between the
two APIs without relearning the vocabulary. `lookups=True` gives every scalar
field the usual `exact`/`i_contains`/`gte` family for free, so only the fields
worth filtering on are declared here.
"""

from typing import Annotated

import strawberry
import strawberry_django
from strawberry_django import DateFilterLookup, StrFilterLookup

from netbox.graphql.filters import PrimaryModelFilter

from netbox_quotes import models

__all__ = (
    'QuoteVendorFilter',
    'QuoteFilter',
    'QuoteLineFilter',
)


@strawberry_django.filter_type(models.QuoteVendor, lookups=True)
class QuoteVendorFilter(PrimaryModelFilter):
    name: StrFilterLookup | None = strawberry_django.filter_field()


@strawberry_django.filter_type(models.Quote, lookups=True)
class QuoteFilter(PrimaryModelFilter):
    number: StrFilterLookup | None = strawberry_django.filter_field()
    status: StrFilterLookup | None = strawberry_django.filter_field()
    currency: StrFilterLookup | None = strawberry_django.filter_field()
    quote_date: DateFilterLookup[str] | None = strawberry_django.filter_field()
    valid_until: DateFilterLookup[str] | None = strawberry_django.filter_field()
    vendor: Annotated['QuoteVendorFilter', strawberry.lazy('netbox_quotes.graphql.filters')] | None = (
        strawberry_django.filter_field()
    )


@strawberry_django.filter_type(models.QuoteLine, lookups=True)
class QuoteLineFilter(PrimaryModelFilter):
    # `serial` is the join key between a vendor quote and the hardware it
    # covers, and it is indexed on the model — expect callers to filter on it.
    serial: StrFilterLookup | None = strawberry_django.filter_field()
    part_number: StrFilterLookup | None = strawberry_django.filter_field()
    service_sku: StrFilterLookup | None = strawberry_django.filter_field()
    match_state: StrFilterLookup | None = strawberry_django.filter_field()
    coverage_start: DateFilterLookup[str] | None = strawberry_django.filter_field()
    coverage_end: DateFilterLookup[str] | None = strawberry_django.filter_field()
    quote: Annotated['QuoteFilter', strawberry.lazy('netbox_quotes.graphql.filters')] | None = (
        strawberry_django.filter_field()
    )
