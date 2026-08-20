"""Resolving what a unit actually costs at a given site.

The refresh report renders a row per hardware model but sums money per site,
because the same box costs different money in different countries. Doing that
naively is a price lookup per (model, site) pair with a region-tree walk
inside — this module loads everything once and answers from dicts, the same
shape as compliance.StandardResolver.

Precedence, most specific wins:

    site price  >  nearest enclosing region's price  >  lifecycle baseline

"Nearest enclosing" walks the region tree upward from the site's region, so a
price on EMEA covers a London site under EMEA North unless EMEA North (or
London itself) has its own.
"""

from dataclasses import dataclass
from decimal import Decimal

from dcim.models import Region

from netbox_refresh.models import ReplacementPrice

__all__ = ('PriceHit', 'PriceResolver')


@dataclass(frozen=True)
class PriceHit:
    """One resolved unit price, and where it came from."""

    cost: Decimal
    currency: str
    # 'site', 'region', or 'base' — the report says which prices are overrides
    # so a surprising number can be traced to the record that produced it.
    source: str


class PriceResolver:
    """Bulk price resolution for a set of lifecycles, three queries total."""

    def __init__(self, lifecycle_ids):
        self._by_site = {}     # (lifecycle_id, site_id) -> PriceHit
        self._by_region = {}   # (lifecycle_id, region_id) -> PriceHit
        self._counts = {}      # lifecycle_id -> number of regional prices
        for price in ReplacementPrice.objects.filter(lifecycle_id__in=lifecycle_ids):
            hit_source = 'site' if price.site_id else 'region'
            hit = PriceHit(cost=price.cost, currency=price.currency, source=hit_source)
            if price.site_id:
                self._by_site[(price.lifecycle_id, price.site_id)] = hit
            else:
                self._by_region[(price.lifecycle_id, price.region_id)] = hit
            self._counts[price.lifecycle_id] = self._counts.get(price.lifecycle_id, 0) + 1

        # The whole region tree as a parent map. Loading all of it is cheaper
        # than being clever: region tables are small (countries and areas, not
        # devices), and a dict walk per lookup beats a query per lookup.
        self._parents = dict(Region.objects.values_list('id', 'parent_id'))

    def price_count(self, lifecycle_id) -> int:
        """How many regional prices this lifecycle has — 0 means baseline only."""
        return self._counts.get(lifecycle_id, 0)

    def resolve(self, lifecycle, site) -> PriceHit | None:
        """The unit price for this lifecycle's model at this site.

        None means no price is known anywhere — not even a baseline — which
        the report has to surface rather than treat as zero: a missing price
        understates the total silently, and an understated refresh budget is
        the expensive kind of wrong.
        """
        hit = self._by_site.get((lifecycle.pk, site.pk if site else None))
        if hit is not None:
            return hit

        region_id = site.region_id if site else None
        seen = set()
        while region_id is not None and region_id not in seen:
            seen.add(region_id)
            hit = self._by_region.get((lifecycle.pk, region_id))
            if hit is not None:
                return hit
            region_id = self._parents.get(region_id)

        if lifecycle.replacement_cost is not None:
            return PriceHit(
                cost=lifecycle.replacement_cost,
                currency=lifecycle.currency,
                source='base',
            )
        return None
