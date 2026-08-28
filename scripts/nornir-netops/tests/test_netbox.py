from types import SimpleNamespace

import pytest

from netops.netbox import (
    AmbiguousSource,
    Client,
    NetBoxError,
    NetBoxInventory,
    device_data,
    feature_of,
    parse_filters,
    platform_of,
    resolve_sources,
    source_for,
)


class FakeClient:
    """Answers the two endpoints the inventory reads."""

    def __init__(self, devices=None, interfaces=None):
        self.devices = devices or []
        self.interfaces = interfaces or {}
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path.startswith("dcim/devices"):
            return self.devices
        field = next((k[3:] for k in (params or {}) if k.startswith("cf_")), None)
        return self.interfaces.get(field, [])


def device(id=1, name="sw1", address="10.1.1.1/24", platform="cisco-ios", **extra):
    record = {
        "id": id,
        "name": name,
        "primary_ip4": {"address": address} if address else None,
        "platform": {"slug": platform} if platform else None,
        "site": {"slug": "atl"},
        "role": {"slug": "core"},
        "status": {"value": "active"},
        "tags": [{"slug": "managed"}],
    }
    record.update(extra)
    return record


def interface(name, device_id=1):
    return {"name": name, "device": {"id": device_id, "name": f"sw{device_id}"}}


# --------------------------------------------------------------------------- #
# devices
# --------------------------------------------------------------------------- #


def test_devices_become_hosts():
    inventory = NetBoxInventory(client=FakeClient([device()])).load()
    host = inventory.hosts["sw1"]
    assert host.hostname == "10.1.1.1"  # the mask is not part of the address
    assert host.platform == "cisco_ios"


@pytest.mark.parametrize(
    "slug,expected",
    [
        ("cisco-ios", "cisco_ios"),
        ("cisco-ios-xe", "cisco_ios"),
        ("arista-eos", "arista_eos"),
        ("eos", "arista_eos"),
    ],
)
def test_netbox_platform_slugs_map_to_netmiko_names(slug, expected):
    """NetBox writes slugs with hyphens; netmiko uses underscores."""
    assert platform_of({"platform": {"slug": slug}}) == expected


def test_a_device_with_no_platform_is_left_for_autodetection():
    inventory = NetBoxInventory(client=FakeClient([device(platform=None)])).load()
    assert inventory.hosts["sw1"].platform is None


def test_a_device_with_no_primary_ip_is_skipped():
    """There is nothing to connect to."""
    client = FakeClient([device(), device(id=2, name="sw2", address=None)])
    inventory = NetBoxInventory(client=client).load()
    assert list(inventory.hosts) == ["sw1"]


def test_site_role_and_tags_become_filterable_data():
    data = device_data(device())
    assert data["site"] == "atl"
    assert data["role"] == "core"
    assert data["tags"] == "managed"


def test_the_older_device_role_key_is_understood():
    """NetBox renamed device_role to role in 3.6."""
    record = device()
    record["device_role"] = record.pop("role")
    assert device_data(record)["role"] == "core"


def test_device_custom_fields_become_data():
    data = device_data(device(custom_fields={"maintenance_window": "sun-0200"}))
    assert data["maintenance_window"] == "sun-0200"


def test_only_active_devices_with_an_address_are_asked_for():
    client = FakeClient([device()])
    NetBoxInventory(client=client).load()
    path, params = client.calls[0]
    assert params["status"] == "active"
    assert params["has_primary_ip"] == "true"


def test_filters_reach_the_query():
    client = FakeClient([device()])
    NetBoxInventory(client=client, filters={"site": "atl"}).load()
    assert client.calls[0][1]["site"] == "atl"


def test_no_devices_is_an_error_that_says_why():
    with pytest.raises(NetBoxError, match="check the filters"):
        NetBoxInventory(client=FakeClient([])).load()


# --------------------------------------------------------------------------- #
# the interface custom field
# --------------------------------------------------------------------------- #


def test_feature_of_a_field_name():
    assert feature_of("ntp_source_interface") == "ntp"
    assert feature_of("syslog_source_interface") == "syslog"


def test_exactly_one_marked_interface_is_the_source():
    single, ambiguous = resolve_sources([interface("Loopback0")], "ntp_source_interface")
    assert single == {1: "Loopback0"}
    assert ambiguous == {}


def test_two_marked_interfaces_are_ambiguous_not_a_choice():
    """Picking one would be guessing, and the wrong source quietly breaks
    return traffic."""
    single, ambiguous = resolve_sources(
        [interface("Loopback0"), interface("Vlan10")], "ntp_source_interface"
    )
    assert single == {}
    assert ambiguous == {1: ["Loopback0", "Vlan10"]}


def test_no_marked_interface_means_no_source():
    single, ambiguous = resolve_sources([], "ntp_source_interface")
    assert (single, ambiguous) == ({}, {})


def test_the_source_lands_on_the_host():
    client = FakeClient([device()], {"ntp_source_interface": [interface("Loopback0")]})
    inventory = NetBoxInventory(client=client).load()
    assert inventory.hosts["sw1"].data["source_interface"]["ntp"] == "Loopback0"


def test_an_ambiguous_device_carries_the_problem_not_a_guess():
    client = FakeClient(
        [device()],
        {"ntp_source_interface": [interface("Loopback0"), interface("Vlan10")]},
    )
    inventory = NetBoxInventory(client=client).load()
    problem = inventory.hosts["sw1"].data["source_interface_error"]["ntp"]
    assert "2 interfaces" in problem
    assert "Loopback0, Vlan10" in problem
    assert "exactly one may be" in problem


def test_one_query_per_field_not_per_device():
    """Asking per device would be a round trip per device per standard."""
    devices = [device(id=i, name=f"sw{i}") for i in range(1, 51)]
    client = FakeClient(devices, {"ntp_source_interface": [interface("Loopback0", 3)]})
    NetBoxInventory(client=client, source_fields=("ntp_source_interface",)).load()
    assert len(client.calls) == 2  # one for devices, one for the field


def test_each_device_gets_its_own_answer():
    client = FakeClient(
        [device(), device(id=2, name="sw2"), device(id=3, name="sw3")],
        {"ntp_source_interface": [interface("Loopback0", 1), interface("Vlan10", 2)]},
    )
    hosts = NetBoxInventory(client=client).load().hosts
    assert hosts["sw1"].data["source_interface"]["ntp"] == "Loopback0"
    assert hosts["sw2"].data["source_interface"]["ntp"] == "Vlan10"
    assert hosts["sw3"].data["source_interface"] == {}  # asked, and the answer is none


# --------------------------------------------------------------------------- #
# reading the answer back
# --------------------------------------------------------------------------- #


def host(**data):
    return SimpleNamespace(name="sw1", data=data)


def test_a_csv_host_has_no_opinion():
    """So the fleet-wide value from the standards file still applies."""
    assert source_for(host(site="atl"), "ntp") == (None, False)


def test_netbox_saying_nothing_is_still_an_answer():
    """`not set` means no source interface, not "fall back to the file"."""
    assert source_for(host(source_interface={}), "ntp") == (None, True)


def test_netbox_naming_an_interface():
    assert source_for(host(source_interface={"ntp": "Loopback0"}), "ntp") == (
        "Loopback0",
        True,
    )


def test_an_ambiguous_device_raises_for_that_standard_only():
    subject = host(
        source_interface={"syslog": "Vlan10"},
        source_interface_error={"ntp": "2 interfaces are marked ntp_source_interface"},
    )
    with pytest.raises(AmbiguousSource, match="2 interfaces"):
        source_for(subject, "ntp")
    assert source_for(subject, "syslog") == ("Vlan10", True)  # unaffected


# --------------------------------------------------------------------------- #
# filters and paging
# --------------------------------------------------------------------------- #


def test_filters_parse():
    assert parse_filters(["site=atl", "role=core"]) == {"site": "atl", "role": "core"}


def test_a_repeated_key_means_any_of_them():
    assert parse_filters(["site=atl", "site=rdu"]) == {"site": ["atl", "rdu"]}


def test_a_malformed_filter_is_rejected():
    with pytest.raises(NetBoxError, match="needs KEY=VALUE"):
        parse_filters(["site"])


def test_a_client_needs_a_url_and_a_token():
    with pytest.raises(NetBoxError, match="NETBOX_URL"):
        Client("", "token")
    with pytest.raises(NetBoxError, match="NETBOX_TOKEN"):
        Client("https://netbox.example.com", "")


def test_paging_follows_next():
    class Paged:
        def __init__(self):
            self.pages = [
                {"results": [device()], "next": "https://nb/api/dcim/devices/?offset=1"},
                {"results": [device(id=2, name="sw2")], "next": None},
            ]
            self.headers = {}

        def get(self, url, params=None, timeout=None, verify=None):
            page = self.pages.pop(0)
            return SimpleNamespace(status_code=200, json=lambda: page, text="")

    client = Client("https://nb", "token")
    client._session = Paged()
    assert len(client.get("dcim/devices/")) == 2
