"""The plugin's slice of the GraphQL Query type.

NetBox merges every entry of `schema` into its single top-level Query class
(netbox/graphql/schema.py: `*registry['plugins']['graphql_schemas']`), so these
fields sit alongside the core ones at /graphql/ with no separate endpoint.

`schema` must be a LIST — the registry extends with it rather than appending.
ComplianceConfig points at this module explicitly (`graphql_schema =
'graphql.schema.schema'`) instead of relying on the default lookup, because the
default resolves the attribute `schema` on the package
netbox_compliance.graphql, where it collides with this very module's name.
Same trap, same fix, as netbox_quotes and netbox_refresh.
"""

import strawberry
import strawberry_django

from netbox_compliance.graphql.types import ConfigComplianceType, ConfigStandardType

__all__ = ('ComplianceQuery', 'schema')


@strawberry.type(name='Query')
class ComplianceQuery:
    config_standard: ConfigStandardType = strawberry_django.field()
    config_standard_list: list[ConfigStandardType] = strawberry_django.field()

    config_compliance: ConfigComplianceType = strawberry_django.field()
    config_compliance_list: list[ConfigComplianceType] = strawberry_django.field()


schema = [ComplianceQuery]
