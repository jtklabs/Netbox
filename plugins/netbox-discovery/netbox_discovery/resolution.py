"""Answer "whose job is this address?" from NetBox's own data.

This is the inverse of what the scanner does on a sweep. The scanner asks
"which addresses are mine?" given a poller; here we have one address and need
the poller. Both walk the same chain, and they must agree — if they disagree,
a device gets onboarded by a poller that will never scan it again, or by none.

The chain, most specific first:

    address -> most specific containing prefix
            -> that prefix's site
            -> site's poller-<name> tag, else the nearest tagged ancestor region

Region tags are inherited by walking *up* from the site, not by expanding a
tagged region downwards, so a sub-region tagged for another poller correctly
takes its sites back from a parent tagged for us. That matches selection.py.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from dcim.models import Region, Site
from ipam.models import Prefix

from netbox_discovery.utils import plugin_setting


@dataclass
class Resolution:
    """What we could work out about an address, and what stopped us."""

    address: str
    prefix: Prefix | None = None
    site: Site | None = None
    poller_name: str = ''
    problem: str = ''

    @property
    def ok(self) -> bool:
        return bool(self.poller_name) and self.site is not None


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


def containing_prefix(address: str) -> Prefix | None:
    """The most specific prefix containing `address`.

    Most specific, not first: a /16 regional aggregate and the /24 that is
    actually the wiring closet both contain the address, and only the /24
    identifies the site. Candidates are few, so they are ranked in Python
    rather than in SQL.
    """
    candidates = list(
        Prefix.objects.filter(prefix__net_contains_or_equals=address)
        .select_related('scope_type')
    )
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.prefix.prefixlen)


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
    prefix = plugin_setting('poller_tag_prefix')

    own = _poller_tag(site.tags.all(), prefix)
    if own:
        return own

    region = site.region
    seen = set()
    while region is not None and region.pk not in seen:
        seen.add(region.pk)
        tag = _poller_tag(region.tags.all(), prefix)
        if tag:
            return tag
        region = region.parent
    return ''


def _poller_tag(tags, prefix: str) -> str:
    """Return the poller name from the first poller-* tag, without its prefix."""
    for tag in tags:
        slug = (tag.slug or '').lower()
        if slug.startswith(prefix):
            return slug[len(prefix):]
    return ''


def resolve(address: str) -> Resolution:
    """Work out which poller owns an address, explaining any failure.

    Every failure here is one an operator can fix, so each says what to do
    rather than just refusing. A silent "no poller" would leave the request
    sitting in the queue forever with nothing to look at.
    """
    try:
        host = normalise_address(address)
    except ValueError as exc:
        return Resolution(address=address, problem=str(exc))

    prefix = containing_prefix(host)
    if prefix is None:
        return Resolution(
            address=host,
            problem=(
                'No prefix in IPAM contains %s, so there is nothing to say which '
                'site it is at. Create the prefix and scope it to a site, then '
                'try again.' % host
            ),
        )

    site = site_for_prefix(prefix)
    if site is None:
        return Resolution(
            address=host, prefix=prefix,
            problem=(
                'Prefix %s is not scoped to a site (or to a location within one), '
                'so the device has no site to be created at. Set its scope.'
                % prefix.prefix
            ),
        )

    poller_name = poller_for_site(site)
    if not poller_name:
        return Resolution(
            address=host, prefix=prefix, site=site,
            problem=(
                'Site %s has no %s* tag, and neither does any region above it, '
                'so no poller is responsible for it. Tag the site or its region.'
                % (site.name, plugin_setting('poller_tag_prefix'))
            ),
        )

    return Resolution(address=host, prefix=prefix, site=site, poller_name=poller_name)


def sites_for_poller(poller_name: str) -> list[Site]:
    """Every site a poller owns, for showing its coverage.

    Resolved per site rather than by querying the tag, because a site inherits
    from its region and that inheritance cannot be expressed as a tag filter.
    """
    return [
        site for site in Site.objects.select_related('region').prefetch_related('tags')
        if poller_for_site(site) == poller_name
    ]
