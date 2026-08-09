"""Work out which addresses this poller is responsible for scanning.

Pollers are not configured with a list of subnets. They ask NetBox what belongs
to them, using tags, so adding a site to a poller's workload is a tag in the UI
rather than a config change on a box somebody has to SSH into.

The ownership rule is a precedence chain:

    device tag  >  site tag  >  region tag

A device tagged `poller-boston` belongs to the Boston poller even if it sits in
a site tagged `poller-dallas`. A site tagged `poller-dallas` belongs to Dallas
even if its region is tagged `poller-boston`. Regions nest, so the region walk
goes upwards from the site and stops at the first tagged ancestor.

"Belongs to somebody else" is detected structurally rather than from a list:
any tag whose slug starts with `poller-` and is not ours claims the object for
another poller. That means standing up a new poller never requires editing the
existing pollers' configuration.

Ownership is resolved object by object in Python rather than with clever API
filters. The tables involved (regions, sites) are small, the logic is fiddly
enough to be worth reading in one place, and NetBox's tag filters AND together
rather than OR, so the query-side approach would need one request per tag
anyway.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field

from .netbox import NetBox

log = logging.getLogger(__name__)

POLLER_TAG_PREFIX = "poller-"


@dataclass
class Target:
    """One address this poller should scan."""

    address: str
    site_id: int | None = None
    site_name: str = ""
    device_id: int | None = None
    device_name: str = ""
    source: str = ""

    def __hash__(self) -> int:
        return hash(self.address)


@dataclass
class Ownership:
    """Resolved poller ownership for the sites and devices NetBox knows about."""

    our_tag: str
    site_owner: dict[int, str] = field(default_factory=dict)
    our_site_ids: set[int] = field(default_factory=set)
    site_names: dict[int, str] = field(default_factory=dict)

    def owns_site(self, site_id: int | None) -> bool:
        return site_id is not None and site_id in self.our_site_ids


def poller_tag(poller_name: str) -> str:
    """`boston` -> `poller-boston`. Accepts an already-prefixed name."""
    name = poller_name.strip().lower()
    if name.startswith(POLLER_TAG_PREFIX):
        return name
    return f"{POLLER_TAG_PREFIX}{name}"


def _poller_claim(tags: list[dict]) -> str | None:
    """Return the poller tag on an object, if it carries one."""
    for tag in tags or []:
        slug = (tag.get("slug") or "").lower()
        if slug.startswith(POLLER_TAG_PREFIX):
            return slug
    return None


def _poller_claims(tags: list[dict]) -> list[str]:
    return [
        (tag.get("slug") or "").lower()
        for tag in tags or []
        if (tag.get("slug") or "").lower().startswith(POLLER_TAG_PREFIX)
    ]


def resolve_ownership(netbox: NetBox, poller_name: str) -> Ownership:
    """Decide which sites belong to this poller.

    Walks every site once, applying site tags first and falling back to the
    nearest tagged ancestor region.
    """
    our_tag = poller_tag(poller_name)
    ownership = Ownership(our_tag=our_tag)

    regions = netbox.all("/dcim/regions/", {"brief": 0})
    region_tags: dict[int, list[str]] = {}
    region_parent: dict[int, int | None] = {}
    for region in regions:
        region_tags[region["id"]] = _poller_claims(region.get("tags", []))
        parent = region.get("parent")
        region_parent[region["id"]] = parent["id"] if parent else None

    sites = netbox.all("/dcim/sites/")
    for site in sites:
        site_id = site["id"]
        ownership.site_names[site_id] = site.get("name", "")
        claims = _poller_claims(site.get("tags", []))
        owner = _pick_owner(claims, our_tag)
        if owner is None:
            region = site.get("region")
            owner = _region_owner(region["id"] if region else None, region_tags, region_parent, our_tag)
        if owner is not None:
            ownership.site_owner[site_id] = owner
            if owner == our_tag:
                ownership.our_site_ids.add(site_id)

    log.info(
        "poller %s owns %d of %d sites", our_tag, len(ownership.our_site_ids), len(sites)
    )
    return ownership


def _pick_owner(claims: list[str], our_tag: str) -> str | None:
    """Our tag wins if present; otherwise the first other poller's claim does.

    An object tagged for two pollers is ambiguous. Resolving it in our favour
    when one of them is us is the safe reading — the alternative is a device
    nobody scans.
    """
    if not claims:
        return None
    if our_tag in claims:
        return our_tag
    return claims[0]


def _region_owner(region_id: int | None, region_tags: dict[int, list[str]],
                  region_parent: dict[int, int | None], our_tag: str) -> str | None:
    """Walk up the region tree, returning the nearest tagged ancestor's owner."""
    seen = set()
    current = region_id
    while current is not None and current not in seen:
        seen.add(current)
        owner = _pick_owner(region_tags.get(current, []), our_tag)
        if owner is not None:
            return owner
        current = region_parent.get(current)
    return None


def select_targets(netbox: NetBox, poller_name: str, scan_tag: str = "",
                   include_device_primaries: bool = True,
                   ownership: Ownership | None = None) -> list[Target]:
    """Return every address this poller should scan, de-duplicated.

    Two sources, unioned:

      * IP addresses in IPAM that fall inside a prefix scoped to one of our
        sites. This is how a newly imported address gets scanned before any
        device exists for it — the prefix tells us the site, the site tells us
        the poller.
      * Devices already in NetBox at our sites that have a primary IP, so a
        device whose address was never imported into IPAM still gets rescanned.

    Devices explicitly tagged for another poller are removed from both.
    """
    ownership = ownership or resolve_ownership(netbox, poller_name)
    our_tag = ownership.our_tag

    if scan_tag and not netbox.tag_exists(scan_tag):
        # Filtering on a non-existent tag is a 400 from NetBox, and silently
        # scanning everything instead would be worse than saying so.
        raise ValueError(
            f"scan tag {scan_tag!r} does not exist in NetBox — create it, or clear "
            "scan_tag in the config to scan every address at our sites"
        )

    targets: dict[str, Target] = {}

    for target in _targets_from_ipam(netbox, ownership, scan_tag):
        targets.setdefault(target.address, target)

    if include_device_primaries:
        for target in _targets_from_devices(netbox, ownership):
            targets.setdefault(target.address, target)

    for target in _targets_from_tagged_devices(netbox, our_tag):
        # A device tagged for us anywhere overrides whatever its site said, so
        # this one replaces rather than defers.
        targets[target.address] = target

    excluded = _addresses_claimed_by_others(netbox, our_tag)
    for address in excluded:
        if address in targets:
            log.debug("skipping %s — claimed by another poller", address)
            targets.pop(address)

    ordered = sorted(targets.values(), key=lambda t: _sort_key(t.address))
    log.info("%s: %d targets selected", our_tag, len(ordered))
    return ordered


def _targets_from_ipam(netbox: NetBox, ownership: Ownership, scan_tag: str) -> list[Target]:
    """IP addresses inside prefixes scoped to our sites.

    `?site_id=` on prefixes is used rather than `?scope_type=dcim.site` because
    it also picks up prefixes scoped to a *location* inside the site, which is
    how larger sites are usually modelled. Both were checked against a live
    4.6.7 instance.
    """
    out: list[Target] = []
    for site_id in sorted(ownership.our_site_ids):
        site_name = ownership.site_names.get(site_id, "")
        prefixes = netbox.all("/ipam/prefixes/", {"site_id": site_id})
        for prefix in prefixes:
            params = {"parent": prefix["prefix"]}
            if scan_tag:
                params["tag"] = scan_tag
            for ip in netbox.all("/ipam/ip-addresses/", params):
                address = _bare_address(ip.get("address", ""))
                if not address:
                    continue
                assigned = ip.get("assigned_object") or {}
                device = (assigned.get("device") or {}) if isinstance(assigned, dict) else {}
                out.append(Target(
                    address=address,
                    site_id=site_id,
                    site_name=site_name,
                    device_id=device.get("id"),
                    device_name=device.get("name", ""),
                    source="ipam",
                ))
    return out


def _targets_from_devices(netbox: NetBox, ownership: Ownership) -> list[Target]:
    """Existing devices at our sites that already have a primary IP."""
    out: list[Target] = []
    for site_id in sorted(ownership.our_site_ids):
        site_name = ownership.site_names.get(site_id, "")
        for device in netbox.all("/dcim/devices/", {"site_id": site_id, "has_primary_ip": "true"}):
            if _poller_claim_excludes(device, ownership.our_tag):
                continue
            address = _bare_address((device.get("primary_ip4") or device.get("primary_ip") or {}).get("address", ""))
            if not address:
                continue
            out.append(Target(
                address=address,
                site_id=site_id,
                site_name=site_name,
                device_id=device["id"],
                device_name=device.get("name", ""),
                source="device-primary-ip",
            ))
    return out


def _targets_from_tagged_devices(netbox: NetBox, our_tag: str) -> list[Target]:
    """Devices tagged for us anywhere, regardless of which site they are in."""
    if not netbox.tag_exists(our_tag):
        # No device has ever been tagged for this poller. Not an error — most
        # pollers are driven entirely by site and region tags.
        return []
    out: list[Target] = []
    for device in netbox.all("/dcim/devices/", {"tag": our_tag}):
        address = _bare_address((device.get("primary_ip4") or device.get("primary_ip") or {}).get("address", ""))
        if not address:
            continue
        site = device.get("site") or {}
        out.append(Target(
            address=address,
            site_id=site.get("id"),
            site_name=site.get("name", ""),
            device_id=device["id"],
            device_name=device.get("name", ""),
            source="device-tag",
        ))
    return out


def _addresses_claimed_by_others(netbox: NetBox, our_tag: str) -> set[str]:
    """Primary addresses of devices explicitly tagged for a different poller.

    Every `poller-*` tag other than ours is enumerated from the tag list, so a
    poller added later is excluded correctly without this poller being
    reconfigured.
    """
    other_tags = [
        tag["slug"] for tag in netbox.all("/extras/tags/")
        if tag.get("slug", "").startswith(POLLER_TAG_PREFIX) and tag["slug"] != our_tag
    ]
    claimed: set[str] = set()
    for slug in other_tags:
        for device in netbox.all("/dcim/devices/", {"tag": slug}):
            if our_tag in [t.get("slug") for t in device.get("tags", [])]:
                # Tagged for both: resolved in our favour, same as _pick_owner.
                continue
            address = _bare_address(
                (device.get("primary_ip4") or device.get("primary_ip") or {}).get("address", "")
            )
            if address:
                claimed.add(address)
    return claimed


def _poller_claim_excludes(device: dict, our_tag: str) -> bool:
    """True when a device's own tags hand it to a different poller."""
    claims = _poller_claims(device.get("tags", []))
    if not claims:
        return False
    return our_tag not in claims


def _bare_address(value: str) -> str:
    """`10.0.0.1/24` -> `10.0.0.1`. SNMP is spoken to a host, not a network."""
    if not value:
        return ""
    return value.split("/")[0].strip()


def _sort_key(address: str):
    try:
        return (0, ipaddress.ip_address(address))
    except ValueError:
        return (1, address)
