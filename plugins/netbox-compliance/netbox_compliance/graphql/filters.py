"""GraphQL filters.

Mirrors the REST filtersets rather than reproducing them: strawberry-django
generates the per-field lookups from the model when `lookups=True`, so only the
relations and the fields people actually filter on are spelled out here.

`status`, `is_stale` and `needs_manual_fix` are deliberately NOT filterable.
Each folds a column together with a plugin setting or a related object in
Python, so there is no column to filter on — and a filter that silently only
worked on some rows would be worse than not having one. Filter on the facts
underneath (result, exempt, last_checked, standard) or use the REST filterset,
which does the fold server-side in SQL.
"""

from typing import TYPE_CHECKING, Annotated

import strawberry
import strawberry_django
from netbox.graphql.filters import PrimaryModelFilter
from strawberry_django import DateFilterLookup, DatetimeFilterLookup, StrFilterLookup

from netbox_compliance import models

if TYPE_CHECKING:
    from dcim.graphql.filters import DeviceFilter, DeviceRoleFilter, PlatformFilter, SiteFilter

__all__ = (
    'ConfigStandardFilter',
    'ConfigComplianceFilter',
)


@strawberry_django.filter_type(models.ConfigStandard, lookups=True)
class ConfigStandardFilter(PrimaryModelFilter):
    name: StrFilterLookup | None = strawberry_django.filter_field()
    check_type: StrFilterLookup | None = strawberry_django.filter_field()
    match_pattern: StrFilterLookup | None = strawberry_django.filter_field()
    auto_remediable: bool | None = strawberry_django.filter_field()
    allow_enforce: bool | None = strawberry_django.filter_field()
    valid_from: DateFilterLookup | None = strawberry_django.filter_field()
    valid_to: DateFilterLookup | None = strawberry_django.filter_field()
    platforms: Annotated['PlatformFilter', strawberry.lazy('dcim.graphql.filters')] | None = (
        strawberry_django.filter_field()
    )
    roles: Annotated['DeviceRoleFilter', strawberry.lazy('dcim.graphql.filters')] | None = (
        strawberry_django.filter_field()
    )
    sites: Annotated['SiteFilter', strawberry.lazy('dcim.graphql.filters')] | None = (
        strawberry_django.filter_field()
    )


@strawberry_django.filter_type(models.ConfigCompliance, lookups=True)
class ConfigComplianceFilter(PrimaryModelFilter):
    result: StrFilterLookup | None = strawberry_django.filter_field()
    source: StrFilterLookup | None = strawberry_django.filter_field()
    exempt: bool | None = strawberry_django.filter_field()
    last_checked: DatetimeFilterLookup | None = strawberry_django.filter_field()
    last_remediated: DatetimeFilterLookup | None = strawberry_django.filter_field()
    exempt_review_by: DateFilterLookup | None = strawberry_django.filter_field()
    device: Annotated['DeviceFilter', strawberry.lazy('dcim.graphql.filters')] | None = (
        strawberry_django.filter_field()
    )
    standard: Annotated[
        'ConfigStandardFilter', strawberry.lazy('netbox_compliance.graphql.filters')
    ] | None = strawberry_django.filter_field()
