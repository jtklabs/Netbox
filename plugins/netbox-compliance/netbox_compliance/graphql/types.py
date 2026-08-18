"""GraphQL object types.

Nova, the portal, is the primary consumer of this data and aggregates over
GraphQL, so both models are exposed here as first-class query roots rather than
being reachable only through REST.

NetBox merges a plugin's schema into the single top-level Query, so these types
appear at the same /graphql/ endpoint and in the same GraphiQL explorer as the
core ones. Object permissions still apply: PrimaryObjectType brings
BaseObjectType.get_queryset, which restricts the queryset for the requesting
user exactly as the REST API does.

Several derived values are exposed as resolved fields — `status`,
`finding_count`, `needs_manual_fix`, `runtime_variables` — because a consumer
asking "which devices need a person to go and fix something" should not have to
re-implement the rules. They resolve per object, so they cannot be filtered or
ordered on; see filters.py.
"""

from typing import TYPE_CHECKING, Annotated

import strawberry
import strawberry_django
from netbox.graphql.types import PrimaryObjectType

from netbox_compliance import models
from netbox_compliance.graphql.filters import ConfigComplianceFilter, ConfigStandardFilter

if TYPE_CHECKING:
    from dcim.graphql.types import DeviceRoleType, DeviceType, PlatformType, SiteType
    from extras.graphql.types import TagType

__all__ = (
    'ConfigStandardType',
    'ConfigComplianceType',
)


@strawberry_django.type(
    models.ConfigStandard,
    fields='__all__',
    filters=ConfigStandardFilter,
    pagination=True,
)
class ConfigStandardType(PrimaryObjectType):
    platforms: list[Annotated['PlatformType', strawberry.lazy('dcim.graphql.types')]]
    roles: list[Annotated['DeviceRoleType', strawberry.lazy('dcim.graphql.types')]]
    sites: list[Annotated['SiteType', strawberry.lazy('dcim.graphql.types')]]
    device_tags: list[Annotated['TagType', strawberry.lazy('extras.graphql.types')]]

    @strawberry_django.field
    def is_active(self) -> bool:
        return self.is_active

    @strawberry_django.field
    def audit_only(self) -> bool:
        """True when no mode may write for this standard — see ConfigStandard."""
        return self.audit_only

    @strawberry_django.field
    def scope_summary(self) -> str:
        """Human-readable scope. "All devices" is a real answer, not an empty one."""
        return self.scope_summary

    @strawberry_django.field
    def runtime_variables(self) -> list[str]:
        """Template variables the checker supplies itself, typically the secret.

        Listed rather than stored: this is how a standard names the accounts
        that should exist without NetBox holding a credential.
        """
        return list(self.runtime_variables)


@strawberry_django.type(
    models.ConfigCompliance,
    fields='__all__',
    filters=ConfigComplianceFilter,
    pagination=True,
)
class ConfigComplianceType(PrimaryObjectType):
    device: Annotated['DeviceType', strawberry.lazy('dcim.graphql.types')]
    standard: Annotated[
        'ConfigStandardType', strawberry.lazy('netbox_compliance.graphql.types')
    ]

    @strawberry_django.field
    def status(self) -> str:
        """The stored result, or the exemption where there is one. One of
        compliant / non-compliant / unknown / error / exempt / exempt-expired."""
        return self.status

    @strawberry_django.field
    def finding_count(self) -> int:
        return self.finding_count

    @strawberry_django.field
    def needs_manual_fix(self) -> bool:
        """Non-compliant, not exempt, and the standard cannot be remediated
        automatically — the rows a person has to act on."""
        return self.needs_manual_fix

    @strawberry_django.field
    def is_stale(self) -> bool:
        return self.is_stale
