"""Answer "whose job is this address?" from NetBox's own data.

This is the inverse of what the scanner does on a sweep. The scanner asks
"which addresses are mine?" given a poller; here we have one address and need
the poller. Both walk the same chain, and they must agree — if they disagree, a
device gets onboarded by a poller that will never scan it again, or by none.

    address (+ tenant) -> most specific containing prefix
                       -> that prefix's site
                       -> site's poller-<name> tag, else nearest tagged ancestor region

Tenant is part of the key, not decoration. Address space overlaps across
companies we have bought: two prefixes of 10.10.1.0/24 can and do coexist in
NetBox's global table — it does not enforce uniqueness there — and a containment
lookup returns both. Choosing between them by mask length alone would be a coin
toss that files an acquired company's switch under our site, or hands it to a
poller with no route to it.

So the rule is: if the candidate prefixes span more than one tenant and the
caller did not say which, refuse and list them. Guessing is the one thing that
must not happen. Where a single tenant owns all the candidates, the most
specific wins as usual and nobody has to type anything.

Region tags are inherited by walking *up* from the site, so a sub-region tagged
for another poller correctly takes its sites back from a parent tagged for us.
That matches selection.py.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from dcim.models import Region, Site
from ipam.models import Prefix

from netbox_discovery.utils import plugin_setting


@dataclass
class Resolution:
    """What we could work out about an address, and what stopped us."""

    address: str
    prefix: Prefix | None = None
    site: Site | None = None
    tenant: object | None = None
    poller_name: str = ''
    problem: str = ''
    # True when no prefix matched and the configured default region supplied
    # the poller. The scan can go ahead; the site is chosen at review.
    used_default_region: bool = False
    candidates: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Enough to queue the request — a poller. A site may still be missing.

        Deliberately not requiring a site: an address that fell back to the
        default region has nowhere to be created yet, but it can still be
        scanned, and the reviewer picks the site before approving. Refusing
        outright would mean the operator gets no information at all about a
        device we could perfectly well go and look at.
        """
        return bool(self.poller_name)


def normalise_address(value: str) -> str:
    """Accept what a person types and return a bare host address.

    People paste `10.10.1.5/24` as readily as `10.10.1.5`, and both mean the
    same host here. SNMP is spoken to a host, never to a network.
    """
    text = (value or '').strip()
    if not text:
        raise ValueError('Enter an IP address.')
    try:
        return str(ipaddress.ip_interface(text).ip)
    except ValueError:
        raise ValueError('%s is not an IP address.' % text) from None


def candidate_prefixes(address: str, tenant=None, vrf=None) -> list[Prefix]:
    """Every prefix that contains `address`, narrowed by tenant and VRF."""
    queryset = Prefix.objects.filter(
        prefix__net_contains_or_equals=address
    ).select_related('tenant', 'vrf', 'scope_type')
    if tenant is not None:
        queryset = queryset.filter(tenant=tenant)
    if vrf is not None:
        queryset = queryset.filter(vrf=vrf)
    return list(queryset)


def most_specific(prefixes: list[Prefix]) -> list[Prefix]:
    """The longest-mask prefixes — plural, because ties are the interesting case."""
    if not prefixes:
        return []
    longest = max(p.prefix.prefixlen for p in prefixes)
    return [p for p in prefixes if p.prefix.prefixlen == longest]


def site_for_prefix(prefix: Prefix) -> Site | None:
    """The site a prefix belongs to.

    NetBox denormalises this: a prefix scoped to a *location* still populates
    `_site`, which is what we want — larger sites model their prefixes against
    locations, and those prefixes still identify the site unambiguously. A
    prefix scoped only to a region has no site and cannot place a device.
    """
    return prefix._site


def poller_for_site(site: Site) -> str:
    """The poller tag owning a site: its own tag, else the nearest tagged region."""
    return _poller_from_tags(site.tags.all()) or poller_for_region(site.region)


def poller_for_region(region: Region | None) -> str:
    """Walk up the region tree, returning the nearest tagged ancestor's poller."""
    seen = set()
    while region is not None and region.pk not in seen:
        seen.add(region.pk)
        found = _poller_from_tags(region.tags.all())
        if found:
            return found
        region = region.parent
    return ''


def _poller_from_tags(tags) -> str:
    prefix = plugin_setting('poller_tag_prefix')
    for tag in tags:
        slug = (tag.slug or '').lower()
        if slug.startswith(prefix):
            return slug[len(prefix):]
    return ''


def default_region() -> Region | None:
    """The region that catches addresses no prefix claims.

    Configured as a slug so it survives the region being renamed. Empty means
    no fallback, and an unmatched address is refused as before.
    """
    slug = (plugin_setting('default_region') or '').strip()
    if not slug:
        return None
    return Region.objects.filter(slug=slug).prefetch_related('tags').first()


def resolve(address: str, tenant=None, vrf=None) -> Resolution:
    """Work out which poller owns an address, explaining any failure.

    Every failure names the thing to fix. A silent "no poller" would leave the
    request in the queue forever with nothing to look at.
    """
    try:
        host = normalise_address(address)
    except ValueError as exc:
        return Resolution(address=address, problem=str(exc))

    candidates = candidate_prefixes(host, tenant=tenant, vrf=vrf)

    if not candidates:
        return _fall_back(host, tenant, vrf)

    # Overlapping space: the address sits in prefixes belonging to more than
    # one owner, and nobody said which device is meant.
    #
    # Keyed on (tenant, VRF) rather than tenant alone, because VRF is what
    # actually holds overlapping space in NetBox. With ENFORCE_GLOBAL_UNIQUE on
    # — the default — two identical prefixes cannot both sit in the global
    # table at all, whatever their tenants; Prefix.clean() refuses the second.
    # Duplicated space therefore lives in separate VRFs, with tenant as the
    # ownership label on top. Either narrows this, so both are accepted.
    owners = {(p.tenant_id, p.vrf_id) for p in candidates}
    if len(owners) > 1 and tenant is None and vrf is None:
        return Resolution(
            address=host, tenant=tenant, candidates=candidates,
            problem=(
                'This address is inside %d prefixes with different owners, so '
                'there is no way to tell which device it is. Choose a tenant or '
                'a VRF. Candidates: %s'
                % (len(owners), _describe(candidates))
            ),
        )

    winners = most_specific(candidates)
    if len(winners) > 1:
        return Resolution(
            address=host, tenant=tenant, candidates=winners,
            problem=(
                'This address is inside %d equally specific prefixes and nothing '
                'distinguishes them, so a site cannot be chosen. Give them '
                'different tenants or VRFs. Candidates: %s'
                % (len(winners), _describe(winners))
            ),
        )

    prefix = winners[0]
    resolved_tenant = tenant or prefix.tenant

    site = site_for_prefix(prefix)
    if site is None:
        # The prefix exists but is not scoped to a site — try the fallback
        # rather than refusing, since a scan is still worth doing.
        fallback = _fall_back(host, resolved_tenant, vrf, prefix=prefix)
        if fallback.ok:
            return fallback
        return Resolution(
            address=host, prefix=prefix, tenant=resolved_tenant,
            problem=(
                'Prefix %s is not scoped to a site (or to a location within one), '
                'so the device has no site to be created at. Set its scope, or '
                'configure a default region.' % prefix.prefix
            ),
        )

    poller_name = poller_for_site(site)
    if not poller_name:
        fallback = _fall_back(host, resolved_tenant, vrf, prefix=prefix, site=site)
        if fallback.ok:
            return fallback
        return Resolution(
            address=host, prefix=prefix, site=site, tenant=resolved_tenant,
            problem=(
                'Site %s has no %s* tag, and neither does any region above it, '
                'so no poller is responsible for it. Tag the site or its region, '
                'or configure a default region.'
                % (site.name, plugin_setting('poller_tag_prefix'))
            ),
        )

    return Resolution(address=host, prefix=prefix, site=site,
                      tenant=resolved_tenant, poller_name=poller_name)


def _fall_back(host, tenant, vrf, prefix=None, site=None) -> Resolution:
    """Hand an otherwise-unplaceable address to the default region's poller.

    Somebody typing an address we have no prefix for still wants to know what
    is at it. The scan goes ahead and the reviewer supplies the site before
    approving — which the approve path already insists on — so nothing is
    created in the wrong place.
    """
    region = default_region()
    if region is None:
        return Resolution(
            address=host, prefix=prefix, site=site, tenant=tenant,
            problem=(
                'No prefix in IPAM contains %s, so there is nothing to say which '
                'site it is at. Create the prefix and scope it to a site, or set '
                'a default region in the plugin configuration.' % host
            ),
        )

    poller_name = poller_for_region(region)
    if not poller_name:
        return Resolution(
            address=host, prefix=prefix, site=site, tenant=tenant,
            problem=(
                'No prefix contains %s, and the default region %s has no %s* tag, '
                'so no poller can be chosen. Tag that region.'
                % (host, region.name, plugin_setting('poller_tag_prefix'))
            ),
        )

    return Resolution(
        address=host, prefix=prefix, site=site, tenant=tenant,
        poller_name=poller_name, used_default_region=True,
    )


def _describe(prefixes) -> str:
    return '; '.join(
        '%s (tenant %s, VRF %s, site %s)' % (
            p.prefix,
            p.tenant.name if p.tenant else '—',
            p.vrf.name if p.vrf else 'global',
            p._site.name if p._site else '—',
        )
        for p in prefixes[:6]
    )


def sites_for_poller(poller_name: str) -> list[Site]:
    """Every site a poller owns, for showing its coverage.

    Resolved per site rather than by querying the tag, because a site inherits
    from its region and that inheritance cannot be expressed as a tag filter.
    """
    return [
        site for site in Site.objects.select_related('region').prefetch_related('tags')
        if poller_for_site(site) == poller_name
    ]
