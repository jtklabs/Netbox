"""Hardware swaps: a serial that changed must never quietly overwrite the old one.

Serials are what support contracts and quotes are matched on, so a rescan that
finds different metal has to keep the thread on the unit that came out. For a
chassis that means retaining the old Device record; for a module it cannot,
because NetBox requires a module to sit in a bay and the bay is being refilled,
so the audit row is the only trace and has to be written before the overwrite.

Needs a NetBox with the Discovery plugin. Scan results come from recorded walks.
"""

from __future__ import annotations

import copy
import os

import pytest
from conftest import collect_fixture

from snmpinv.model import build_scan_result
from snmpinv.netbox import NetBox
from snmpinv.sync import Syncer, SyncOptions

URL = os.environ.get("SNMPINV_TEST_NETBOX_URL", "")
TOKEN = os.environ.get("SNMPINV_TEST_NETBOX_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not (URL and TOKEN),
    reason="set SNMPINV_TEST_NETBOX_URL and SNMPINV_TEST_NETBOX_TOKEN to run",
)

PREFIX = "SWAP_TEST_"
SLUG = "swap-test-"
REPLACEMENTS = "/plugins/discovery/hardware-replacements/"


@pytest.fixture(scope="module")
def netbox():
    box = NetBox(URL, TOKEN, verify_ssl=False, timeout=30)
    if not box.endpoint_available(REPLACEMENTS):
        pytest.skip("the Discovery plugin is not installed on this NetBox")
    return box


@pytest.fixture(scope="module")
def site(netbox):
    existing = netbox.first("/dcim/sites/", {"slug": SLUG + "dc"})
    site = existing or netbox.create(
        "/dcim/sites/", {"name": PREFIX + "DC", "slug": SLUG + "dc"}
    )
    yield site

    import requests
    session = netbox.session

    def delete(path, oid):
        try:
            session.delete("%s%s%s/" % (netbox.base, path.lstrip("/"), oid), timeout=30)
        except requests.RequestException:
            pass

    for row in netbox.all(REPLACEMENTS):
        if (row.get("new_serial") or "").startswith("SWAP"):
            delete(REPLACEMENTS, row["id"])
    for device in netbox.all("/dcim/devices/", {"site_id": site["id"]}):
        delete("/dcim/devices/", device["id"])
    for vc in netbox.all("/dcim/virtual-chassis/"):
        if vc.get("member_count", 0) == 0:
            delete("/dcim/virtual-chassis/", vc["id"])
    delete("/dcim/sites/", site["id"])


def scan_with(serial, name="swap-sw-01", model="WS-C2960X-48FPD-L"):
    """A single-device scan result with a chosen serial."""
    result = build_scan_result(collect_fixture("cisco-2960x"))
    result.devices = result.devices[:1]
    device = result.devices[0]
    device.name = name
    device.serial = serial
    device.model = model
    device.modules = []
    device.interfaces = device.interfaces[:2]
    return result


def syncer(netbox):
    return Syncer(netbox, SyncOptions(device_role=SLUG + "network"))


class TestChassisSwap:
    def test_first_scan_creates_the_device(self, netbox, site):
        syncer(netbox).sync(scan_with("SWAP-AAAA-0001"), site["id"])
        device = netbox.first("/dcim/devices/", {"serial": "SWAP-AAAA-0001"})
        assert device is not None
        assert device["name"] == "swap-sw-01"
        assert device["status"]["value"] == "active"

    def test_same_serial_again_changes_nothing(self, netbox, site):
        before = netbox.count("/dcim/devices/", {"site_id": site["id"]})
        syncer(netbox).sync(scan_with("SWAP-AAAA-0001"), site["id"])
        assert netbox.count("/dcim/devices/", {"site_id": site["id"]}) == before

    def test_a_changed_serial_retains_the_old_device(self, netbox, site):
        """The requirement: update, but do not lose the unit that came out."""
        syncer(netbox).sync(scan_with("SWAP-BBBB-0002"), site["id"])

        old = netbox.first("/dcim/devices/", {"serial": "SWAP-AAAA-0001"})
        assert old is not None, "the replaced unit must still exist in NetBox"
        assert old["status"]["value"] == "inventory"
        assert "replaced" in [t["slug"] for t in old["tags"]]
        assert old["name"] != "swap-sw-01", "the old record must give up the name"
        assert "SWAP-AAAA-0001" in old["name"]

    def test_the_new_unit_took_the_name(self, netbox, site):
        new = netbox.first("/dcim/devices/", {"serial": "SWAP-BBBB-0002"})
        assert new is not None
        assert new["name"] == "swap-sw-01"
        assert new["status"]["value"] == "active"

    def test_the_swap_was_recorded(self, netbox, site):
        rows = netbox.all(REPLACEMENTS, {"new_serial": "SWAP-BBBB-0002"})
        assert len(rows) == 1
        row = rows[0]
        assert row["old_serial"] == "SWAP-AAAA-0001"
        # The nested device serializer is brief and carries no serial, so the
        # link is checked by id against the devices themselves.
        new = netbox.first("/dcim/devices/", {"serial": "SWAP-BBBB-0002"})
        old = netbox.first("/dcim/devices/", {"serial": "SWAP-AAAA-0001"})
        assert row["device"]["id"] == new["id"]
        assert row["replaced_device"] is not None, (
            "a chassis swap should point at the retained record"
        )
        assert row["replaced_device"]["id"] == old["id"]

    def test_the_old_serial_is_still_findable(self, netbox, site):
        """The whole point — a contract matched on the old serial still resolves."""
        assert netbox.count("/dcim/devices/", {"serial": "SWAP-AAAA-0001"}) == 1

    def test_filling_a_blank_serial_is_not_a_swap(self, netbox, site):
        """First successful read of a device that had no serial is not a
        replacement, and must not retire anything."""
        blank = netbox.create("/dcim/devices/", {
            "name": "swap-sw-blank",
            "device_type": netbox.first("/dcim/device-types/",
                                        {"model": "WS-C2960X-48FPD-L"})["id"],
            "role": netbox.first("/dcim/device-roles/", {"slug": SLUG + "network"})["id"],
            "site": site["id"],
        })
        syncer(netbox).sync(scan_with("SWAP-CCCC-0003", name="swap-sw-blank"), site["id"])
        after = netbox.get("/dcim/devices/%s/" % blank["id"])
        assert after["serial"] == "SWAP-CCCC-0003"
        assert after["status"]["value"] == "active", "nothing should have been retired"
        assert netbox.count(REPLACEMENTS, {"new_serial": "SWAP-CCCC-0003"}) == 0


class TestModuleSwap:
    """A module cannot be retained — its bay is being refilled — so the audit
    row has to be written before the serial is overwritten."""

    def scan_with_module(self, serial, module_serial):
        result = build_scan_result(collect_fixture("cisco-c9300-stack"))
        result.devices = result.devices[:1]
        result.virtual_chassis_name = ""
        device = result.devices[0]
        device.name = "swap-mod-01"
        device.serial = serial
        device.vc_position = None
        device.vc_is_master = False
        device.interfaces = device.interfaces[:2]
        device.modules = device.modules[:1]
        device.modules[0].serial = module_serial
        return result

    def test_module_swap_is_recorded_and_serial_updated(self, netbox, site):
        syncer(netbox).sync(self.scan_with_module("SWAP-DDDD-0004", "SWAP-MOD-1111"),
                            site["id"])
        device = netbox.first("/dcim/devices/", {"serial": "SWAP-DDDD-0004"})
        modules = netbox.all("/dcim/modules/", {"device_id": device["id"]})
        assert modules and modules[0]["serial"] == "SWAP-MOD-1111"

        # The line card is swapped.
        syncer(netbox).sync(self.scan_with_module("SWAP-DDDD-0004", "SWAP-MOD-2222"),
                            site["id"])
        modules = netbox.all("/dcim/modules/", {"device_id": device["id"]})
        assert len(modules) == 1, "the bay still holds exactly one module"
        assert modules[0]["serial"] == "SWAP-MOD-2222", "the new part is recorded"

        rows = netbox.all(REPLACEMENTS, {"new_serial": "SWAP-MOD-2222"})
        assert len(rows) == 1, "the removed module's serial must survive somewhere"
        assert rows[0]["old_serial"] == "SWAP-MOD-1111"
        assert rows[0]["module_bay"]
        assert rows[0]["replaced_device"] is None, (
            "a module swap keeps no separate device record — that is why the row exists"
        )
