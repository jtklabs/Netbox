"""NetBox poller ownership, matching snmp-inventory's selection rules.

Device tags override site/nearest-region tags. Existing device primaries and
addresses inside prefixes scoped to our sites are unioned by the scanner. A
NetBox device primary is already an IPAM address, so testing prefix membership
avoids retrieving every IPAM address in those networks here. No network scan
or discovery-plugin dependency is needed.
"""
from __future__ import annotations

import ipaddress
import os
import re

from .netbox import NetBoxError, _address

PREFIX = "poller-"


def poller_tag(name):
    value = str(name or "").strip().lower()
    bare = value[len(PREFIX):] if value.startswith(PREFIX) else value
    if not bare or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", bare):
        raise NetBoxError("--netbox-autofilter requires --poller NAME or NETOPS_POLLER (a poller name/tag slug)")
    return PREFIX + bare


def settings_from(args):
    enabled = getattr(args, "netbox_autofilter", None)
    if enabled is None:
        value = os.environ.get("NETBOX_AUTOFILTER", "false").strip().lower()
        if value not in ("true", "false"):
            raise NetBoxError("NETBOX_AUTOFILTER must be true or false")
        enabled = value == "true"
    name = getattr(args, "poller", None) or os.environ.get("NETOPS_POLLER")
    return {"autofilter": enabled, "poller": poller_tag(name) if enabled else None}


def claims(record):
    # Brief/field-restricted API payloads must not silently hide an override.
    tags = record.get("tags")
    if not isinstance(tags, list) or any(not isinstance(t, dict) or not isinstance(t.get("slug"), str) for t in tags):
        raise NetBoxError("poller autofilter requires full NetBox objects with tag slugs; remove brief/fields filters")
    return [t["slug"].lower() for t in tags if t["slug"].lower().startswith(PREFIX)]


def pick(tags, ours):
    # SNMP inventory explicitly includes objects tagged for both pollers.
    return ours if ours in tags else next(iter(tags), None)


def reference(value):
    return value.get("id") if isinstance(value, dict) else None


def sites_owned(client, ours):
    regions = client.get("dcim/regions/")  # Never brief, even brief=0.
    sites = client.get("dcim/sites/")
    region_map = {}
    for region in regions:
        if "id" not in region or "parent" not in region:
            raise NetBoxError("poller autofilter requires complete NetBox region records")
        region_map[region["id"]] = (claims(region), reference(region.get("parent")))
    owned = {}
    for site in sites:
        if "id" not in site or "region" not in site:
            raise NetBoxError("poller autofilter requires complete NetBox site records")
        owner = pick(claims(site), ours)
        evidence = {"source": "site-tag", "site_id": site["id"]}
        current, seen = reference(site.get("region")), set()
        while owner is None and current is not None:
            if current in seen:
                raise NetBoxError("poller autofilter found a cycle in NetBox region ancestry")
            seen.add(current)
            if current not in region_map:
                raise NetBoxError(f"poller autofilter cannot read region {current}; check NetBox permissions")
            tags, parent = region_map[current]
            owner = pick(tags, ours)
            evidence = {"source": "region-tag", "region_id": current, "site_id": site["id"]}
            current = parent
        if owner == ours:
            owned[site["id"]] = evidence
    return owned


def select_devices(client, devices, name):
    """Return selected rows plus the evidence for each selected device ID.

    Like SNMP selection, site membership and owned-prefix membership are a
    union, not a longest-prefix override. Explicit foreign device tags always
    exclude a device from both sources.
    """
    ours = poller_tag(name)
    sites = sites_owned(client, ours)
    selected, pending = {}, []
    for device in devices:
        if device.get("id") is None:
            raise NetBoxError("poller autofilter requires NetBox device IDs")
        owner = pick(claims(device), ours)
        if not _address(device):
            continue
        if owner == ours:
            selected[device["id"]] = {"source": "device-tag"}
        elif owner is not None:
            continue
        elif reference(device.get("site")) in sites:
            selected[device["id"]] = dict(sites[reference(device.get("site"))])
        else:
            try:
                pending.append((device, ipaddress.ip_address(_address(device))))
            except ValueError as exc:
                raise NetBoxError(f"poller autofilter: invalid primary address on {device.get('name')}") from exc

    # Query the same site_id prefix scope as snmp-inventory: NetBox includes
    # prefixes scoped to a location within the site, as well as site scopes.
    networks = []
    if pending:
        for site_id in sorted(sites):
            for prefix in client.get("ipam/prefixes/", {"site_id": site_id}):
                try:
                    network = ipaddress.ip_network(prefix["prefix"], strict=False)
                except (KeyError, TypeError, ValueError) as exc:
                    raise NetBoxError("poller autofilter: invalid prefix returned by NetBox") from exc
                networks.append((network, {**sites[site_id], "source": "prefix-" + sites[site_id]["source"],
                                           "prefix": str(network), "prefix_id": prefix.get("id")}))
    for device, address in pending:
        for network, evidence in networks:
            if address in network:
                selected[device["id"]] = dict(evidence)
                break
    for evidence in selected.values():
        evidence["poller_tag"] = ours
    return [d for d in devices if d["id"] in selected], selected
