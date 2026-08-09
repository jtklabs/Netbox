"""Poller ownership: device tag > site tag > region tag.

This runs against an in-memory stand-in for NetBox rather than a live one. The
precedence rules have a lot of cases and each needs a differently shaped tree;
building those by hand in a real instance would be slow and the assertions
would be about setup rather than about the rule. The live instance is exercised
separately in test_netbox_live.py, which is what confirms the query shapes are
real.

The topology under test, with `b` = poller-boston (ours) and `d` = poller-dallas:

    americas [b]
      us-east
        site-inherits-b            -> ours, inherited two levels up
        nyc [d]
          site-in-their-region     -> theirs
          site-overrides-b [b]     -> ours, site tag beats region tag
      site-overrides-d [d]         -> theirs, site tag beats region tag
    emea [d]
      site-emea                    -> theirs
    (no region)
      site-orphan                  -> nobody's
      site-tagged-b [b]            -> ours
"""

from __future__ import annotations

import pytest

from snmpinv.selection import (
    poller_tag,
    resolve_ownership,
    select_targets,
)

OURS = "poller-boston"
THEIRS = "poller-dallas"


def tags(*slugs):
    return [{"slug": slug, "name": slug} for slug in slugs]


class FakeNetBox:
    """Just enough of the NetBox client for selection to run against."""

    def __init__(self, regions, sites, devices, prefixes=(), ip_addresses=(), tag_slugs=()):
        self.regions = regions
        self.sites = sites
        self.devices = devices
        self.prefixes = list(prefixes)
        self.ip_addresses = list(ip_addresses)
        self.tags = [{"slug": s, "name": s} for s in tag_slugs]
        self.queries: list[tuple[str, dict]] = []

    def all(self, path, params=None):
        params = params or {}
        self.queries.append((path, params))
        if path == "/dcim/regions/":
            return list(self.regions)
        if path == "/dcim/sites/":
            return list(self.sites)
        if path == "/extras/tags/":
            return list(self.tags)
        if path == "/ipam/prefixes/":
            site_id = params.get("site_id")
            return [p for p in self.prefixes if p["_site_id"] == site_id]
        if path == "/ipam/ip-addresses/":
            parent = params.get("parent")
            wanted_tag = params.get("tag")
            out = [ip for ip in self.ip_addresses if ip["_prefix"] == parent]
            if wanted_tag:
                out = [ip for ip in out
                       if wanted_tag in [t["slug"] for t in ip.get("tags", [])]]
            return out
        if path == "/dcim/devices/":
            out = list(self.devices)
            if "site_id" in params:
                out = [d for d in out if (d.get("site") or {}).get("id") == params["site_id"]]
            if params.get("has_primary_ip") == "true":
                out = [d for d in out if d.get("primary_ip4")]
            if "tag" in params:
                out = [d for d in out
                       if params["tag"] in [t["slug"] for t in d.get("tags", [])]]
            return out
        raise AssertionError(f"unexpected path {path}")

    def first(self, path, params=None):
        results = self.all(path, params)
        return results[0] if results else None

    def count(self, path, params=None):
        if path == "/extras/tags/" and params and "slug" in params:
            return sum(1 for t in self.tags if t["slug"] == params["slug"])
        return len(self.all(path, params))

    def tag_exists(self, slug):
        return self.count("/extras/tags/", {"slug": slug}) > 0


REGIONS = [
    {"id": 1, "name": "americas", "parent": None, "tags": tags(OURS)},
    {"id": 2, "name": "us-east", "parent": {"id": 1}, "tags": []},
    {"id": 3, "name": "nyc", "parent": {"id": 2}, "tags": tags(THEIRS)},
    {"id": 5, "name": "emea", "parent": None, "tags": tags(THEIRS)},
]

SITES = [
    {"id": 10, "name": "site-inherits-b", "region": {"id": 2}, "tags": []},
    {"id": 11, "name": "site-in-their-region", "region": {"id": 3}, "tags": []},
    {"id": 12, "name": "site-overrides-b", "region": {"id": 3}, "tags": tags(OURS)},
    {"id": 13, "name": "site-overrides-d", "region": {"id": 1}, "tags": tags(THEIRS)},
    {"id": 14, "name": "site-emea", "region": {"id": 5}, "tags": []},
    {"id": 15, "name": "site-orphan", "region": None, "tags": []},
    {"id": 16, "name": "site-tagged-b", "region": None, "tags": tags(OURS)},
]


def device(id, name, site_id, address=None, device_tags=()):
    entry = {
        "id": id, "name": name,
        "site": {"id": site_id, "name": f"site{site_id}"},
        "tags": tags(*device_tags),
    }
    if address:
        entry["primary_ip4"] = {"id": id * 100, "address": address}
    return entry


DEVICES = [
    device(1, "sw-inherits", 10, "10.0.10.1"),
    device(2, "sw-their-device-at-our-site", 10, "10.0.10.2", (THEIRS,)),
    device(3, "sw-our-device-at-their-site", 11, "10.0.11.3", (OURS,)),
    device(4, "sw-in-their-site", 13, "10.0.13.4"),
    device(5, "sw-no-primary-ip", 10),
]


def make_netbox(**overrides):
    kwargs = dict(
        regions=REGIONS, sites=SITES, devices=DEVICES,
        tag_slugs=(OURS, THEIRS, "scan"),
    )
    kwargs.update(overrides)
    return FakeNetBox(**kwargs)


class TestPollerTag:
    def test_prefixes_bare_name(self):
        assert poller_tag("boston") == "poller-boston"

    def test_leaves_prefixed_name_alone(self):
        assert poller_tag("poller-boston") == "poller-boston"

    def test_case_insensitive(self):
        assert poller_tag("Boston") == "poller-boston"


class TestOwnership:
    def setup_method(self):
        self.ownership = resolve_ownership(make_netbox(), "boston")

    def test_region_tag_is_inherited_through_untagged_children(self):
        """site-inherits-b sits in us-east, which has no tag; the owner comes
        from americas, two levels up."""
        assert 10 in self.ownership.our_site_ids

    def test_nearest_tagged_ancestor_wins(self):
        """nyc is tagged for Dallas inside a region tagged for us — the nearer
        tag wins, so sites in nyc are theirs."""
        assert 11 not in self.ownership.our_site_ids
        assert self.ownership.site_owner[11] == THEIRS

    def test_site_tag_beats_region_tag_in_our_favour(self):
        assert 12 in self.ownership.our_site_ids

    def test_site_tag_beats_region_tag_against_us(self):
        assert 13 not in self.ownership.our_site_ids
        assert self.ownership.site_owner[13] == THEIRS

    def test_untagged_site_with_no_region_belongs_to_nobody(self):
        assert 15 not in self.ownership.our_site_ids
        assert 15 not in self.ownership.site_owner

    def test_site_tagged_ours_with_no_region(self):
        assert 16 in self.ownership.our_site_ids

    def test_exactly_the_expected_sites(self):
        assert self.ownership.our_site_ids == {10, 12, 16}

    def test_ambiguous_tags_resolve_in_our_favour(self):
        """A site tagged for two pollers, one of them us, is ours. The
        alternative is a device nobody scans."""
        sites = [dict(s) for s in SITES]
        sites[0] = {**sites[0], "tags": tags(THEIRS, OURS)}
        ownership = resolve_ownership(make_netbox(sites=sites), "boston")
        assert 10 in ownership.our_site_ids

    def test_region_cycle_does_not_hang(self):
        """A region tree that points at itself must terminate, not spin."""
        regions = [
            {"id": 1, "name": "a", "parent": {"id": 2}, "tags": []},
            {"id": 2, "name": "b", "parent": {"id": 1}, "tags": []},
        ]
        sites = [{"id": 10, "name": "s", "region": {"id": 1}, "tags": []}]
        ownership = resolve_ownership(make_netbox(regions=regions, sites=sites), "boston")
        assert ownership.our_site_ids == set()


class TestTargetSelection:
    def test_devices_at_our_sites_are_selected(self):
        targets = select_targets(make_netbox(), "boston", include_device_primaries=True)
        addresses = {t.address for t in targets}
        assert "10.0.10.1" in addresses

    def test_device_tagged_for_another_poller_is_excluded_at_our_site(self):
        """The whole point of the precedence chain: their device, our site."""
        targets = select_targets(make_netbox(), "boston")
        assert "10.0.10.2" not in {t.address for t in targets}

    def test_device_tagged_for_us_is_included_at_their_site(self):
        targets = select_targets(make_netbox(), "boston")
        assert "10.0.11.3" in {t.address for t in targets}

    def test_device_at_a_site_we_do_not_own_is_excluded(self):
        targets = select_targets(make_netbox(), "boston")
        assert "10.0.13.4" not in {t.address for t in targets}

    def test_device_without_primary_ip_is_skipped(self):
        targets = select_targets(make_netbox(), "boston")
        assert all(t.address for t in targets)
        assert len(targets) == 2

    def test_addresses_are_bare_not_cidr(self):
        targets = select_targets(make_netbox(), "boston")
        assert all("/" not in t.address for t in targets)

    def test_ipam_addresses_inside_our_prefixes_are_selected(self):
        prefixes = [{"id": 1, "prefix": "10.0.10.0/24", "_site_id": 10}]
        ips = [
            {"id": 1, "address": "10.0.10.50/24", "_prefix": "10.0.10.0/24",
             "tags": tags("scan"), "assigned_object": None},
            {"id": 2, "address": "10.0.10.51/24", "_prefix": "10.0.10.0/24",
             "tags": [], "assigned_object": None},
        ]
        netbox = make_netbox(prefixes=prefixes, ip_addresses=ips)
        targets = select_targets(netbox, "boston", scan_tag="scan")
        addresses = {t.address for t in targets}
        assert "10.0.10.50" in addresses
        # Untagged address is filtered out when a scan tag is configured.
        assert "10.0.10.51" not in addresses

    def test_no_scan_tag_selects_every_address_in_our_prefixes(self):
        prefixes = [{"id": 1, "prefix": "10.0.10.0/24", "_site_id": 10}]
        ips = [
            {"id": 2, "address": "10.0.10.51/24", "_prefix": "10.0.10.0/24",
             "tags": [], "assigned_object": None},
        ]
        netbox = make_netbox(prefixes=prefixes, ip_addresses=ips)
        targets = select_targets(netbox, "boston", scan_tag="")
        assert "10.0.10.51" in {t.address for t in targets}

    def test_ipam_target_carries_its_site(self):
        prefixes = [{"id": 1, "prefix": "10.0.10.0/24", "_site_id": 10}]
        ips = [{"id": 1, "address": "10.0.10.50/24", "_prefix": "10.0.10.0/24",
                "tags": [], "assigned_object": None}]
        netbox = make_netbox(prefixes=prefixes, ip_addresses=ips)
        target = [t for t in select_targets(netbox, "boston")
                  if t.address == "10.0.10.50"][0]
        assert target.site_id == 10
        assert target.source == "ipam"

    def test_missing_scan_tag_raises_rather_than_scanning_everything(self):
        """NetBox 400s on an unknown tag slug. Silently scanning every address
        instead would be a much worse failure than stopping."""
        netbox = make_netbox(tag_slugs=(OURS, THEIRS))
        with pytest.raises(ValueError, match="does not exist"):
            select_targets(netbox, "boston", scan_tag="scan")

    def test_our_poller_tag_never_queried_when_it_does_not_exist(self):
        """Filtering devices by a tag nobody has created yet is a 400."""
        netbox = make_netbox(tag_slugs=(THEIRS,))
        select_targets(netbox, "boston")
        device_tag_queries = [
            params for path, params in netbox.queries
            if path == "/dcim/devices/" and params.get("tag") == OURS
        ]
        assert device_tag_queries == []

    def test_results_are_deduplicated_and_sorted(self):
        prefixes = [{"id": 1, "prefix": "10.0.10.0/24", "_site_id": 10}]
        # The same address reachable through both IPAM and a device primary IP.
        ips = [{"id": 1, "address": "10.0.10.1/24", "_prefix": "10.0.10.0/24",
                "tags": [], "assigned_object": None}]
        netbox = make_netbox(prefixes=prefixes, ip_addresses=ips)
        targets = select_targets(netbox, "boston")
        addresses = [t.address for t in targets]
        assert len(addresses) == len(set(addresses))
        assert addresses == sorted(addresses, key=lambda a: tuple(int(p) for p in a.split(".")))

    def test_new_only_skips_device_rescans(self):
        targets = select_targets(make_netbox(), "boston", include_device_primaries=False)
        # Only the device explicitly tagged for us survives; site-derived
        # device rescans are skipped.
        assert {t.address for t in targets} == {"10.0.11.3"}
