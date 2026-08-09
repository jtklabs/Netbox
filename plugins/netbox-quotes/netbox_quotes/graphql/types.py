"""GraphQL types for the quotes models.

Why this exists: the portal that consumes NetBox aggregates data from several
systems and renders composed views — a device page wants the device, its
interfaces, its lifecycle and its support coverage together. Over REST that is
a fan-out of separate calls the caller has to stitch; over GraphQL it is one
query. Without these types the plugin's data is invisible to NetBox's GraphQL
API, so a consumer that has otherwise standardised on GraphQL is forced back to
REST for our models alone — the worst of both.

Fields are enumerated rather than declared with `fields='__all__'`, for two
reasons. It keeps the exposed surface deliberate: this is a published contract,
and a field should appear because someone decided it should. And `__all__`
would drag in the generic-relation plumbing (`assigned_object_type`,
`assigned_object_id`) as raw columns, which is not what a caller wants — see
`device` below for what they actually want.
"""

from typing import Annotated, TYPE_CHECKING

import strawberry
import strawberry_django

from netbox.graphql.types import PrimaryObjectType

from netbox_quotes import models
from netbox_quotes.graphql.filters import (
    QuoteFilter,
    QuoteLineFilter,
    QuoteVendorFilter,
)

if TYPE_CHECKING:
    from dcim.graphql.types import DeviceType

__all__ = (
    'QuoteVendorType',
    'QuoteType',
    'QuoteLineType',
)


@strawberry_django.type(
    models.QuoteVendor,
    fields=('id', 'name', 'portal_url', 'description', 'comments'),
    filters=QuoteVendorFilter,
    pagination=True,
)
class QuoteVendorType(PrimaryObjectType):
    quotes: list[Annotated['QuoteType', strawberry.lazy('netbox_quotes.graphql.types')]]


@strawberry_django.type(
    models.Quote,
    fields=(
        'id',
        'number',
        'status',
        'quote_date',
        'valid_until',
        'currency',
        'description',
        'comments',
    ),
    filters=QuoteFilter,
    pagination=True,
)
class QuoteType(PrimaryObjectType):
    vendor: Annotated['QuoteVendorType', strawberry.lazy('netbox_quotes.graphql.types')] | None
    lines: list[Annotated['QuoteLineType', strawberry.lazy('netbox_quotes.graphql.types')]]

    @strawberry.field
    def total(self) -> str | None:
        """Sum of the quote's line totals.

        Returned as a string, matching the REST serializer: these are Decimals
        and a GraphQL Float would silently round money. Callers should parse it
        as a decimal, not a float.
        """
        total = self.total
        return str(total) if total is not None else None

    @strawberry.field
    def document_url(self) -> str | None:
        """Direct link to the uploaded quote document, if there is one.

        The FileField itself is not exposed: a caller wants somewhere to send
        the user, not the storage path.
        """
        if not self.document:
            return None
        return self.document.url


@strawberry_django.type(
    models.QuoteLine,
    fields=(
        'id',
        'line_number',
        'part_number',
        'service_sku',
        'serial',
        'quantity',
        'unit_price',
        'line_total',
        'coverage_start',
        'coverage_end',
        'match_state',
        'description',
        'comments',
    ),
    filters=QuoteLineFilter,
    pagination=True,
)
class QuoteLineType(PrimaryObjectType):
    quote: Annotated['QuoteType', strawberry.lazy('netbox_quotes.graphql.types')] | None

    @strawberry.field
    def device(self) -> Annotated['DeviceType', strawberry.lazy('dcim.graphql.types')] | None:
        """The device this line ultimately covers.

        A line is matched by serial to a Device, a Module or an InventoryItem.
        The last two are components, and the question a caller is really asking
        is "which device does this cover?" — so resolve the parent rather than
        exposing the generic relation and making every consumer walk it.
        """
        return self.device

    @strawberry.field
    def effective_total(self) -> str | None:
        """Line total, falling back to quantity x unit price when not set.

        A string for the same reason as `Quote.total` — this is money.
        """
        total = self.effective_total
        return str(total) if total is not None else None
