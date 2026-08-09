"""GraphQL query entry points for the quotes plugin.

NetBox merges `schema` into its root Query as extra base classes
(`*registry['plugins']['graphql_schemas']` in netbox/graphql/schema.py), so it
must be a LIST — the registration helper calls `.extend()` on it.
"""

import strawberry
import strawberry_django

from netbox_quotes.graphql.types import (
    QuoteLineType,
    QuoteType,
    QuoteVendorType,
)

__all__ = ('QuotesQuery', 'schema')


@strawberry.type(name='Query')
class QuotesQuery:
    quote_vendor: QuoteVendorType = strawberry_django.field()
    quote_vendor_list: list[QuoteVendorType] = strawberry_django.field()

    quote: QuoteType = strawberry_django.field()
    quote_list: list[QuoteType] = strawberry_django.field()

    quote_line: QuoteLineType = strawberry_django.field()
    quote_line_list: list[QuoteLineType] = strawberry_django.field()


schema = [QuotesQuery]
