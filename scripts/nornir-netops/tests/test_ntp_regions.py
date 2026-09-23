"""NTP servers per NetBox region: the nearest region with its own set wins, else ntp.servers."""
from types import SimpleNamespace

import pytest

from netops.features.ntp import FEATURE, per_device
from netops.netbox import NetBoxInventory, site_regions
from netops.standards import StandardsError

from test_netbox import FakeClient, device
from test_ntp import parse_args

DOCUMENT = {
    "ntp": {
        "servers": ["10.50.0.10", "10.50.0.11"],
        "regions": {
            "us-east": ["10.1.0.10", "10.1.0.11"],
            "emea": {"servers": ["10.2.0.10", "10.2.0.11"], "prefer": "10.2.0.11"},
        },
    }
}


def host(regions=None, region=None, source=None):
    data = {}
    if regions is not None:
        data["regions"] = regions
    if region is not None:
        data["region"] = region
    if source is not None:
        data.update(source_interface={"ntp": source}, source_interface_error={})
    return SimpleNamespace(name="sw1", data=data)


def servers_for(document, target, argv=()):
    desired = FEATURE.build_desired(parse_args(argv, document))
    keys, variables = per_device(list(desired.keys), dict(desired.variables), target)
    return [variables["entries"][k]["host"] for k in keys if variables["entries"][k]["kind"] == "server"], variables


def test_region_gets_its_own_servers():
    servers, variables = servers_for(DOCUMENT, host(regions=["us-east", "us"]))
    assert servers == ["10.1.0.10", "10.1.0.11"]
    assert variables["region"] == "us-east"


def test_nearest_region_with_a_set_wins_through_nesting():
    # A site in atl-metro, under us-east, under us: us-east's set applies.
    assert servers_for(DOCUMENT, host(regions=["atl-metro", "us-east", "us"]))[0] == ["10.1.0.10", "10.1.0.11"]


def test_unlisted_region_falls_back_to_the_default():
    servers, variables = servers_for(DOCUMENT, host(regions=["apac"]))
    assert servers == ["10.50.0.10", "10.50.0.11"]
    assert "region" not in variables


def test_region_prefer_is_used_for_that_region():
    _, variables = servers_for(DOCUMENT, host(regions=["emea"]))
    assert variables["prefer"] == "10.2.0.11"


def test_csv_region_column_works_too():
    assert servers_for(DOCUMENT, host(region="EMEA"))[0] == ["10.2.0.10", "10.2.0.11"]


def test_regions_only_needs_a_match():
    document = {"ntp": {"regions": DOCUMENT["ntp"]["regions"]}}
    assert servers_for(document, host(regions=["us-east"]))[0] == ["10.1.0.10", "10.1.0.11"]
    with pytest.raises(ValueError, match="no NTP servers for this device"):
        servers_for(document, host(regions=["apac"], region="apac"))


def test_servers_flag_overrides_regions():
    assert servers_for(DOCUMENT, host(regions=["us-east"]), ["-s", "10.9.9.9"])[0] == ["10.9.9.9"]


def test_source_interface_still_applies_to_regional_servers():
    desired = FEATURE.build_desired(parse_args((), DOCUMENT))
    keys, variables = per_device(list(desired.keys), dict(desired.variables), host(regions=["us-east"], source="Loopback0"))
    records = [variables["entries"][k] for k in keys]
    assert {r["source"] for r in records if r["kind"] == "server"} == {"Loopback0"}


def test_authentication_order_is_kept(monkeypatch):
    monkeypatch.setenv("NETOPS_NTP_KEY_1", "ntp-key-material")
    document = {"ntp": {**DOCUMENT["ntp"], "authentication": {"key_id": 1}}}
    desired = FEATURE.build_desired(parse_args((), document))
    keys, variables = per_device(list(desired.keys), dict(desired.variables), host(regions=["us-east"]))
    kinds = [variables["entries"][k]["kind"] for k in keys]
    assert kinds == ["key", "trusted-key", "server", "server", "authenticate"]
    assert all(variables["entries"][k].get("key") == "1" for k in keys if variables["entries"][k]["kind"] == "server")


@pytest.mark.parametrize("regions", [["10.1.1.1"], {"us": "10.1.1.1"}, {"us": {"servers": ["10.1.1.1"], "vrf": "x"}},
                                     {"us": []}, {"us": {"servers": ["10.1.1.1"], "prefer": "10.9.9.9"}}])
def test_bad_region_sets_fail_before_any_device(regions):
    with pytest.raises((StandardsError, ValueError)):
        FEATURE.build_desired(parse_args((), {"ntp": {"servers": ["10.50.0.10"], "regions": regions}}))


class RegionClient(FakeClient):
    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "dcim/regions/":
            return [{"id": 1, "slug": "us", "parent": None}, {"id": 2, "slug": "us-east", "parent": {"id": 1}},
                    {"id": 3, "slug": "atl-metro", "parent": {"id": 2}}]
        if path == "dcim/sites/":
            return [{"id": 7, "region": {"id": 3}}, {"id": 8, "region": None}]
        return super().get(path, params)


def test_site_regions_walks_to_the_root():
    client = RegionClient()
    chains = site_regions(client, [device(site={"id": 7, "slug": "atl"}), device(id=2, site={"id": 8, "slug": "lab"})])
    assert chains == {7: ["atl-metro", "us-east", "us"], 8: []}


def test_inventory_adds_regions_only_when_asked():
    client = RegionClient([device(site={"id": 7, "slug": "atl"})])
    data = NetBoxInventory(client=client, regions=True).load().hosts["sw1"].data
    assert (data["region"], data["regions"]) == ("atl-metro", ["atl-metro", "us-east", "us"])
    client = RegionClient([device(site={"id": 7, "slug": "atl"})])
    assert "regions" not in NetBoxInventory(client=client).load().hosts["sw1"].data
    assert not any(path in ("dcim/regions/", "dcim/sites/") for path, _ in client.calls)


def test_plan_notes_which_set_was_used():
    from netops.features.ntp import plan_ntp
    desired = FEATURE.build_desired(parse_args((), DOCUMENT))
    for target, note in ((host(regions=["emea"]), "NTP servers for region emea"),
                         (host(regions=["apac"]), "NTP servers: default set (no region matched)")):
        keys, variables = per_device(list(desired.keys), dict(desired.variables), target)
        context = {"variables": variables, "notes": []}
        plan_ntp([], keys, "add", context)
        assert context["notes"] == [note]
