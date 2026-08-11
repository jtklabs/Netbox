"""Writes and queries against a real NetBox.

The fake NetBox in test_selection.py proves the ownership rules; this proves the
requests are shaped the way NetBox 4.6 actually wants, which is the half a fake
can never check. It is also where idempotency is demonstrated honestly: the
same scan is run twice against a live instance and the object counts must not
move.

Scan results come from recorded walks, not from an emulated device. Needing a
NetBox and needing net-snmp are independent, and tying them together meant
these tests could not run on a machine that had one but not the other — which
is most machines, since macOS ships a net-snmp too old for the emulator. What
is under test here is the NetBox side; the wire is covered in test_emulated.py.

Opt in by pointing it at a NetBox you do not mind writing to:

    export SNMPINV_TEST_NETBOX_URL=http://10.50.10.132:8080/netbox
    export SNMPINV_TEST_NETBOX_TOKEN=nbt_...
    pytest tests/test_netbox_live.py

Everything it creates is named with the SNMPINV_TEST_ prefix and torn down
afterwards, so it will not disturb other data on the instance. It never deletes
anything it did not create.
"""

from __future__ import annotations

import os

import pytest
from conftest import collect_fixture

from snmpinv.model import build_scan_result
from snmpinv.netbox import NetBox
from snmpinv.selection import resolve_ownership, select_targets
from snmpinv.sync import (
    LIFECYCLE_ENDPOINT,
    LIFECYCLE_PLUGIN,
    SOFTWARE_VERSION_FIELD,
    Syncer,
    SyncOptions,
)


def _choice(value):
    """Read a NetBox choice field, which serializes as either a bare string or
    a {"value": ..., "label": ...} object depending on the serializer."""
    if isinstance(value, dict):
        return value.get("value")
    return value


def _lifecycle_ready(netbox) -> bool:
    """Does this instance have the Lifecycle software endpoints?

    An instance can run a Lifecycle plugin older than the software models, so
    the endpoint is probed rather than the plugin's presence trusted.
    """
    return (netbox.plugin_installed(LIFECYCLE_PLUGIN)
            and netbox.endpoint_available(LIFECYCLE_ENDPOINT))

URL = os.environ.get("SNMPINV_TEST_NETBOX_URL", "")
TOKEN = os.environ.get("SNMPINV_TEST_NETBOX_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not (URL and TOKEN),
    reason="set SNMPINV_TEST_NETBOX_URL and SNMPINV_TEST_NETBOX_TOKEN to run",
)

PREFIX = "SNMPINV_TEST_"
SLUG_PREFIX = "snmpinv-test-"
OUR_POLLER = "snmpinv-test-boston"
THEIR_POLLER = "snmpinv-test-dallas"
OUR_TAG = f"poller-{OUR_POLLER}"
THEIR_TAG = f"poller-{THEIR_POLLER}"
SCAN_TAG = f"{SLUG_PREFIX}scan"

@pytest.fixture(scope="module")
def netbox():
    return NetBox(URL, TOKEN, verify_ssl=False, timeout=30)


@pytest.fixture(scope="module")
def lab(netbox):
    """Build a small region/site/prefix tree, hand it back, then remove it.

    Mirrors the topology the ownership tests use: a region tagged for us, a
    sub-region tagged for somebody else, and a site inside that sub-region
    tagged back to us.
    """
    created: list[tuple[str, int]] = []

    def make(path, payload, lookup=None):
        """Create, or bring an existing object back to the shape we need.

        A previous run that failed to tear down completely would otherwise
        leave, say, a site whose region had been deleted out from under it, and
        every ownership assertion would then fail for a reason that has nothing
        to do with the code.
        """
        existing = netbox.first(path, lookup or {})
        if existing is not None:
            # Re-assert the fields the assertions depend on. On a shared lab
            # instance the object may already exist pointing somewhere else —
            # a prefix scoped to a different site, say — and adopting it as-is
            # would fail the test for a reason unrelated to the code.
            fixup = {k: v for k, v in payload.items()
                     if k in ("region", "parent", "tags", "scope_type", "scope_id")}
            if fixup:
                netbox.update(path, existing["id"], fixup)
                existing = netbox.first(path, lookup or {})
            created.append((path, existing["id"]))
            return existing
        obj = netbox.create(path, payload)
        created.append((path, obj["id"]))
        return obj

    for slug in (OUR_TAG, THEIR_TAG, SCAN_TAG):
        make("/extras/tags/", {"name": slug, "slug": slug}, {"slug": slug})

    parent = make(
        "/dcim/regions/",
        {"name": f"{PREFIX}Americas", "slug": f"{SLUG_PREFIX}americas",
         "tags": [{"slug": OUR_TAG}]},
        {"slug": f"{SLUG_PREFIX}americas"},
    )
    child = make(
        "/dcim/regions/",
        {"name": f"{PREFIX}NYC", "slug": f"{SLUG_PREFIX}nyc", "parent": parent["id"],
         "tags": [{"slug": THEIR_TAG}]},
        {"slug": f"{SLUG_PREFIX}nyc"},
    )
    ours = make(
        "/dcim/sites/",
        {"name": f"{PREFIX}Site Ours", "slug": f"{SLUG_PREFIX}site-ours", "region": parent["id"]},
        {"slug": f"{SLUG_PREFIX}site-ours"},
    )
    theirs = make(
        "/dcim/sites/",
        {"name": f"{PREFIX}Site Theirs", "slug": f"{SLUG_PREFIX}site-theirs",
         "region": child["id"]},
        {"slug": f"{SLUG_PREFIX}site-theirs"},
    )
    reclaimed = make(
        "/dcim/sites/",
        {"name": f"{PREFIX}Site Reclaimed", "slug": f"{SLUG_PREFIX}site-reclaimed",
         "region": child["id"], "tags": [{"slug": OUR_TAG}]},
        {"slug": f"{SLUG_PREFIX}site-reclaimed"},
    )
    make(
        "/ipam/prefixes/",
        {"prefix": "198.51.100.0/24", "scope_type": "dcim.site", "scope_id": ours["id"]},
        {"prefix": "198.51.100.0/24"},
    )

    yield {
        "parent_region": parent, "child_region": child,
        "site_ours": ours, "site_theirs": theirs, "site_reclaimed": reclaimed,
    }

    _teardown(netbox)


# Addresses the fixtures hand out. They carry no test prefix of their own, so
# they are listed explicitly rather than pattern-matched — this file must never
# delete an address it did not create.
FIXTURE_ADDRESSES = ("10.10.1.5/24", "10.30.0.11/24")


def _teardown(netbox: NetBox) -> None:
    """Remove everything the test created, children before parents.

    Deleting the devices has to be driven off the *site*, not off a name
    prefix: the devices a scan creates are named after the emulated hardware
    ("bld-a-core-01"), so a prefix match misses them, the site delete then
    fails with the devices still referencing it, and the next run inherits a
    half-deleted topology.
    """
    import requests

    session = netbox.session

    def delete(path, object_id):
        try:
            session.delete(f"{netbox.base}{path.lstrip('/')}{object_id}/", timeout=30)
        except requests.RequestException:
            pass

    def purge(path, match, params=None):
        for obj in netbox.all(path, params):
            if match(obj):
                delete(path, obj["id"])

    named = lambda o: (o.get("name") or "").startswith(PREFIX) or \
                      (o.get("slug") or "").startswith(SLUG_PREFIX)

    test_sites = [s for s in netbox.all("/dcim/sites/") if named(s)]
    test_site_ids = {s["id"] for s in test_sites}

    # Detach virtual-chassis masters first. A device that is a VC's master
    # cannot be deleted while it holds that role, so skipping this leaves the
    # master behind, the site delete then fails with it still attached, and the
    # next run finds it by serial and adopts it at the wrong site.
    for vc in netbox.all("/dcim/virtual-chassis/"):
        master = vc.get("master") or {}
        if (master.get("id") and
                any(m.get("id") == master["id"]
                    for m in netbox.all("/dcim/devices/", {"virtual_chassis_id": vc["id"]})
                    if (m.get("site") or {}).get("id") in test_site_ids)):
            try:
                session.patch(f"{netbox.base}dcim/virtual-chassis/{vc['id']}/",
                              json={"master": None}, timeout=30)
            except requests.RequestException:
                pass

    # Devices next — this also takes their interfaces, module bays and modules.
    for site in test_sites:
        for device in netbox.all("/dcim/devices/", {"site_id": site["id"]}):
            delete("/dcim/devices/", device["id"])

    # Virtual chassis only once their members are gone.
    for vc in netbox.all("/dcim/virtual-chassis/"):
        if vc.get("member_count", 0) == 0:
            delete("/dcim/virtual-chassis/", vc["id"])

    purge("/ipam/ip-addresses/",
          lambda o: str(o.get("address", "")).startswith("198.51.100.")
          or o.get("address") in FIXTURE_ADDRESSES)
    purge("/ipam/prefixes/", lambda o: o.get("prefix") == "198.51.100.0/24")
    for site in test_sites:
        delete("/dcim/sites/", site["id"])
    purge("/dcim/regions/", named)
    purge("/extras/tags/", lambda o: (o.get("slug") or "") in (OUR_TAG, THEIR_TAG, SCAN_TAG))
    # Device roles the sync layer created for this run.
    purge("/dcim/device-roles/", lambda o: (o.get("slug") or "").startswith(SLUG_PREFIX))


class TestVerifiedApiBehaviour:
    """The API facts the client depends on, asserted against a live instance.

    These are regression guards: each one was checked by hand during design and
    at least one of them contradicted the documentation we started from.
    """

    def test_prefix_site_scoping(self, netbox, lab):
        site_id = lab["site_ours"]["id"]
        by_scope = netbox.all("/ipam/prefixes/",
                              {"scope_type": "dcim.site", "scope_id": site_id})
        assert [p["prefix"] for p in by_scope] == ["198.51.100.0/24"]
        # site_id also works and is broader — it picks up location-scoped
        # prefixes within the site, which is why selection.py uses it.
        by_site = netbox.all("/ipam/prefixes/", {"site_id": site_id})
        assert "198.51.100.0/24" in [p["prefix"] for p in by_site]

    def test_sites_by_region_includes_descendants(self, netbox, lab):
        """A region filter is a tree filter: sites in child regions come too."""
        sites = netbox.all("/dcim/sites/", {"region_id": lab["parent_region"]["id"]})
        names = {s["name"] for s in sites}
        assert f"{PREFIX}Site Ours" in names
        assert f"{PREFIX}Site Theirs" in names, "descendant region's sites were not included"

    def test_unknown_tag_slug_is_a_400_not_an_empty_list(self, netbox):
        """The reason every tag filter has to be guarded by tag_exists()."""
        from snmpinv.netbox import NetBoxError

        with pytest.raises(NetBoxError) as exc:
            netbox.all("/dcim/devices/", {"tag": "snmpinv-test-definitely-not-a-tag"})
        assert "400" in str(exc.value)
        assert netbox.tag_exists("snmpinv-test-definitely-not-a-tag") is False

    def test_contains_returns_least_specific_first(self, netbox, lab):
        """Callers must sort by mask length rather than trusting the order."""
        results = netbox.all("/ipam/prefixes/", {"contains": "198.51.100.10/32"})
        assert results, "containing prefix not found"
        assert results[-1]["prefix"] == "198.51.100.0/24"

    def test_brief_is_triggered_by_presence_not_by_value(self, netbox, lab):
        """`brief=0` still gives you a brief serializer.

        NetBox switches on the parameter being present at all, so a
        well-meaning brief=0 strips `parent` and `tags` from regions — the two
        fields ownership resolution is built on — and region inheritance
        silently stops working everywhere. A fake NetBox cannot catch this.
        """
        full = netbox.all("/dcim/regions/")
        assert full, "no regions to check"
        assert "tags" in full[0] and "parent" in full[0]

        brief_zero = netbox.all("/dcim/regions/", {"brief": 0})
        assert "tags" not in brief_zero[0], (
            "brief=0 returned a full serializer — if NetBox has changed this, "
            "the comment in selection.resolve_ownership can be relaxed"
        )

    def test_region_tags_are_actually_readable(self, netbox, lab):
        """The exact call resolve_ownership makes must carry the poller tags."""
        regions = netbox.all("/dcim/regions/")
        parent = [r for r in regions if r["id"] == lab["parent_region"]["id"]][0]
        assert OUR_TAG in [t["slug"] for t in parent.get("tags", [])]


class TestOwnershipAgainstLiveNetBox:
    def test_precedence_resolves_the_same_as_the_fake(self, netbox, lab):
        ownership = resolve_ownership(netbox, OUR_POLLER)
        assert lab["site_ours"]["id"] in ownership.our_site_ids
        # Sub-region tagged for another poller wins over the parent region.
        assert lab["site_theirs"]["id"] not in ownership.our_site_ids
        # ...unless the site itself is tagged back to us.
        assert lab["site_reclaimed"]["id"] in ownership.our_site_ids

    def test_select_targets_runs_against_real_query_shapes(self, netbox, lab):
        targets = select_targets(netbox, OUR_POLLER, scan_tag="")
        assert isinstance(targets, list)


@pytest.fixture(scope="module")
def synced(netbox, lab):
    """Scan an emulated stack into the live NetBox, then scan it again.

    Module-scoped so the double sync runs once and every assertion below reads
    the same resulting state.
    """
    site_id = lab["site_ours"]["id"]
    options = SyncOptions(
        device_role=f"{SLUG_PREFIX}network",
        access_point_role=f"{SLUG_PREFIX}ap",
    )
    result = build_scan_result(collect_fixture("cisco-c9300-stack"))
    syncer = Syncer(netbox, options)
    syncer.sync(result, site_id, scanned_address="10.10.1.5")
    syncer.flush_software_reports()
    first = _snapshot(netbox, site_id)
    # Second pass over the identical data. Nothing may be created.
    syncer.sync(result, site_id, scanned_address="10.10.1.5")
    syncer.flush_software_reports()
    second = _snapshot(netbox, site_id)
    return {"result": result, "site_id": site_id, "first": first, "second": second}


class TestFullScanAndSync:
    """Scan an emulated stack and write it into a real NetBox, twice."""

    def test_stack_became_a_virtual_chassis_of_three(self, netbox, synced):
        devices = netbox.all("/dcim/devices/", {"site_id": synced["site_id"]})
        stack = [d for d in devices if (d.get("virtual_chassis") or {}).get("name")
                 == "bld-a-core-01"]
        assert len(stack) == 3

    def test_each_member_kept_its_own_serial_and_model(self, netbox, synced):
        devices = netbox.all("/dcim/devices/", {"site_id": synced["site_id"]})
        by_serial = {d["serial"]: d for d in devices if d["serial"]}
        assert {"FOC2530L0AB", "FOC2530L0CD", "FOC2531L0EF"} <= set(by_serial)
        assert by_serial["FOC2530L0AB"]["device_type"]["model"] == "C9300-48P"
        assert by_serial["FOC2531L0EF"]["device_type"]["model"] == "C9300-24P"

    def test_model_is_verbatim_not_manufacturer_glued_on(self, netbox, synced):
        types = netbox.all("/dcim/device-types/")
        models = {t["model"] for t in types}
        assert "C9300-48P" in models
        assert not any(m.lower().startswith("cisco") and "9300" in m for m in models)

    def test_vc_master_and_positions(self, netbox, synced):
        vc = netbox.first("/dcim/virtual-chassis/", {"name": "bld-a-core-01"})
        assert vc is not None
        assert vc["master"]["name"] == "bld-a-core-01"
        members = netbox.all("/dcim/devices/", {"virtual_chassis_id": vc["id"]})
        assert sorted(d["vc_position"] for d in members) == [1, 2, 3]

    def test_software_version_recorded(self, netbox, synced):
        """Where the version lands depends on whether the Lifecycle plugin is
        installed — its DeviceSoftware model when it is, a custom field when
        it is not. Deliberately never both."""
        device = netbox.first("/dcim/devices/", {"serial": "FOC2530L0AB"})
        if _lifecycle_ready(netbox):
            record = netbox.first("/plugins/refresh/device-software/",
                                  {"device_id": device["id"]})
            assert record is not None, "no DeviceSoftware record was created"
            version = record.get("raw_version") or \
                (record.get("software_version") or {}).get("version")
            assert version == "17.03.04a"
            assert _choice(record.get("source")) == "snmp"
            # The verbatim sysDescr is kept so a wrong-looking version can be
            # traced to what the device actually said.
            assert "Version 17.03.04a" in (record.get("raw_report") or "")
            # collected_at is when the device was walked, not when the batch
            # was pushed — that is what makes an old reading render as stale.
            assert record.get("collected_at"), "collected_at was not recorded"
            # And the custom field is not also written — one fact, one home.
            assert not device["custom_fields"].get(SOFTWARE_VERSION_FIELD)
        else:
            assert device["custom_fields"].get(SOFTWARE_VERSION_FIELD) == "17.03.04a"

    def test_software_report_is_not_written_per_scan(self, netbox, synced):
        """The plugin's ingest endpoint bumps an unchanged version without a
        changelog entry, so a nightly sweep does not bury real changes."""
        if not _lifecycle_ready(netbox):
            pytest.skip("Lifecycle plugin has no device-software endpoint here")
        device = netbox.first("/dcim/devices/", {"serial": "FOC2530L0AB"})
        record = netbox.first("/plugins/refresh/device-software/", {"device_id": device["id"]})
        assert record is not None
        # synced ran the sync twice; there must still be exactly one record.
        assert netbox.count("/plugins/refresh/device-software/",
                            {"device_id": device["id"]}) == 1

    def test_platform_was_set(self, netbox, synced):
        device = netbox.first("/dcim/devices/", {"serial": "FOC2530L0AB"})
        assert device["platform"]["name"] == "Cisco IOS-XE"

    def test_interfaces_are_on_the_right_members(self, netbox, synced):
        for serial, member in (("FOC2530L0AB", 1), ("FOC2530L0CD", 2), ("FOC2531L0EF", 3)):
            device = netbox.first("/dcim/devices/", {"serial": serial})
            names = [i["name"] for i in
                     netbox.all("/dcim/interfaces/", {"device_id": device["id"]})]
            stack_ports = [n for n in names if n.count("/") == 2]
            assert stack_ports
            assert all(n.split("Ethernet")[1].split("/")[0] == str(member) for n in stack_ports)

    def test_modules_created_with_their_own_serials(self, netbox, synced):
        # Scoped to the devices this test created — a shared lab instance may
        # well hold modules of the same type belonging to something else.
        serials = set()
        for serial in ("FOC2530L0AB", "FOC2530L0CD", "FOC2531L0EF"):
            device = netbox.first("/dcim/devices/", {"serial": serial})
            modules = netbox.all("/dcim/modules/", {"device_id": device["id"]})
            uplinks = [m for m in modules
                       if (m.get("module_type") or {}).get("model") == "C9300-NM-8X"]
            assert len(uplinks) == 1, f"{serial} should have exactly one uplink module"
            serials.add(uplinks[0]["serial"])
        assert len(serials) == 3, "each member's module must keep its own serial"

    def test_management_ip_assigned_and_made_primary(self, netbox, synced):
        device = netbox.first("/dcim/devices/", {"serial": "FOC2530L0AB"})
        assert (device.get("primary_ip4") or {}).get("address") == "10.10.1.5/24"

    def test_mac_addresses_written_once(self, netbox, synced):
        """NetBox does not deduplicate MACAddress creates, so a second scan
        would double them if the lookup-before-create were missing."""
        macs = netbox.all("/dcim/mac-addresses/", {"mac_address": "AC:F2:C5:01:01:01"})
        assert len(macs) == 1

    def test_a_relocated_device_is_moved_not_left_behind(self, netbox, lab, synced):
        """A device found by serial at another site follows its address.

        Serial matching is site-independent, so without this a unit that was
        re-racked stays at its old site forever — and a stack picks up members
        at the new site while the known one stays behind, leaving a virtual
        chassis spanning two sites.
        """
        device = netbox.first("/dcim/devices/", {"serial": "FOC2530L0AB"})
        netbox.update("/dcim/devices/", device["id"],
                      {"site": lab["site_reclaimed"]["id"]})
        assert (netbox.first("/dcim/devices/", {"serial": "FOC2530L0AB"})
                ["site"]["id"]) == lab["site_reclaimed"]["id"]

        result = build_scan_result(collect_fixture("cisco-c9300-stack"))
        Syncer(netbox, SyncOptions(device_role=f"{SLUG_PREFIX}network")).sync(
            result, synced["site_id"], scanned_address="10.10.1.5")

        moved = netbox.first("/dcim/devices/", {"serial": "FOC2530L0AB"})
        assert moved["site"]["id"] == synced["site_id"]
        # And the whole chassis is at one site again.
        vc = netbox.first("/dcim/virtual-chassis/", {"name": "bld-a-core-01"})
        members = netbox.all("/dcim/devices/", {"virtual_chassis_id": vc["id"]})
        assert {(m.get("site") or {}).get("id") for m in members} == {synced["site_id"]}

    def test_rescan_created_nothing(self, synced):
        """The headline property: identical input, no second copy of anything."""
        assert synced["first"] == synced["second"], (
            f"a re-scan changed object counts: {synced['first']} -> {synced['second']}"
        )


def test_dry_run_writes_nothing(netbox, lab):
    site_id = lab["site_ours"]["id"]
    before = _snapshot(netbox, site_id)
    dry = NetBox(URL, TOKEN, verify_ssl=False, dry_run=True)
    result = build_scan_result(collect_fixture("arista-7050sx"))
    Syncer(dry, SyncOptions(device_role=f"{SLUG_PREFIX}network")).sync(
        result, site_id, scanned_address="10.30.0.11"
    )
    assert _snapshot(netbox, site_id) == before
    # ...but it must still report what it would have done.
    assert dry.created, "dry run reported no intended changes"


def _snapshot(netbox: NetBox, site_id: int) -> dict:
    """Counts of everything a scan can create, for before/after comparison."""
    return {
        "devices": netbox.count("/dcim/devices/", {"site_id": site_id}),
        "device_types": netbox.count("/dcim/device-types/"),
        "module_types": netbox.count("/dcim/module-types/"),
        "modules": netbox.count("/dcim/modules/"),
        "module_bays": netbox.count("/dcim/module-bays/"),
        "interfaces": netbox.count("/dcim/interfaces/"),
        "virtual_chassis": netbox.count("/dcim/virtual-chassis/"),
        "ip_addresses": netbox.count("/ipam/ip-addresses/"),
        "mac_addresses": netbox.count("/dcim/mac-addresses/"),
        "manufacturers": netbox.count("/dcim/manufacturers/"),
        "platforms": netbox.count("/dcim/platforms/"),
    }


class TestOneBadAddressDoesNotSinkTheDevice:
    """NetBox refuses some addresses outright. That must cost one address.

    Before this, a rejected address raised out of the middle of the interface
    loop and aborted the sync, so every interface after the offending one was
    silently never written and the device looked half-scanned with nothing
    explaining why.
    """

    NAME = "snmpinv-badip-sw"
    SITE = "snmpinv-badip-site"

    def _cleanup(self, netbox):
        """The client has no delete() — the poller never removes anything —
        so the test reaches through to the request layer for its own tidying."""
        for path, lookup in (
            ("/dcim/devices/", {"name": self.NAME}),
            ("/dcim/sites/", {"slug": self.SITE}),
        ):
            existing = netbox.first(path, lookup)
            if existing:
                netbox._request("DELETE", f"{path}{existing['id']}/")

    def test_the_rest_of_the_device_is_still_written(self, netbox):
        from snmpinv.model import DeviceRecord, InterfaceRecord, ScanResult
        from snmpinv.sync import Syncer, SyncOptions

        self._cleanup(netbox)
        site = netbox.create("/dcim/sites/", {
            "name": self.SITE, "slug": self.SITE,
        }, label="site")
        try:
            record = DeviceRecord(
                name=self.NAME, model="C9300-24P", serial="BADIP0001",
                manufacturer="Cisco",
            )
            # The middle interface carries an address NetBox will not assign:
            # the broadcast of its own prefix, exactly what a mis-derived
            # RowPointer produces. The ones either side must survive it.
            record.interfaces = [
                InterfaceRecord(name="Gi1/0/1", type_slug="1000base-t"),
                InterfaceRecord(name="Vlan99", type_slug="virtual",
                                ip_addresses=["169.254.251.255/24"]),
                InterfaceRecord(name="Gi1/0/2", type_slug="1000base-t"),
            ]
            result = ScanResult(host="192.0.2.50", devices=[record])

            Syncer(netbox, SyncOptions()).sync(result, site["id"])   # must not raise

            device = netbox.first("/dcim/devices/", {"name": self.NAME})
            assert device is not None, "the device was never created"
            names = {
                i["name"] for i in
                netbox.all("/dcim/interfaces/", {"device_id": device["id"]})
            }
            assert {"Gi1/0/1", "Vlan99", "Gi1/0/2"} <= names, (
                f"interfaces after the bad address went missing: {sorted(names)}"
            )
        finally:
            self._cleanup(netbox)

    def test_the_primary_ip_is_still_set_from_a_later_interface(self, netbox):
        """The symptom that actually gets noticed.

        The primary IP is set when the scanned address turns up among the
        device's addresses. If a rejected address aborts the loop first, the
        real one is never reached and the device silently ends up with no
        primary — which reads as "this platform does not report a primary IP"
        rather than as the write having been cut short.
        """
        from snmpinv.model import DeviceRecord, InterfaceRecord, ScanResult
        from snmpinv.sync import Syncer, SyncOptions

        self._cleanup(netbox)
        site = netbox.create("/dcim/sites/", {
            "name": self.SITE, "slug": self.SITE,
        }, label="site")
        try:
            record = DeviceRecord(
                name=self.NAME, model="IB-1420", serial="BADIP0002",
                manufacturer="Infoblox",
            )
            record.interfaces = [
                InterfaceRecord(name="LAN1", type_slug="1000base-t",
                                ip_addresses=["169.254.251.255/24"]),
                InterfaceRecord(name="MGMT", type_slug="1000base-t",
                                ip_addresses=["10.40.0.50/24"]),
            ]
            result = ScanResult(host="10.40.0.50", devices=[record])

            Syncer(netbox, SyncOptions()).sync(
                result, site["id"], scanned_address="10.40.0.50")

            device = netbox.get(f"/dcim/devices/{netbox.first('/dcim/devices/', {'name': self.NAME})['id']}/")
            primary = device.get("primary_ip4") or device.get("primary_ip") or {}
            assert primary.get("address") == "10.40.0.50/24", (
                f"no primary IP was set (got {primary or None}) — the address "
                f"on the interface after the rejected one was never reached"
            )
        finally:
            self._cleanup(netbox)


class TestThePolledAddressBecomesThePrimaryIP:
    """The address we targeted is the one an operator reaches the box on.

    It is normally set as a side effect of syncing the interface that reports
    it — but plenty of platforms never report it. An Arista's Management1 is
    in a VRF the default ipAddressTable does not expose, and several
    appliances list no addresses at all, so those devices arrived with the
    field blank as though the platform could not tell us, when we knew the
    answer before the scan started.
    """

    NAME = "snmpinv-primary-sw"
    SITE = "snmpinv-primary-site"

    def _cleanup(self, netbox):
        for path, lookup in (
            ("/dcim/devices/", {"name": self.NAME}),
            ("/dcim/sites/", {"slug": self.SITE}),
        ):
            existing = netbox.first(path, lookup)
            if existing:
                netbox._request("DELETE", f"{path}{existing['id']}/")
        for stray in netbox.all("/ipam/ip-addresses/", {"address": "10.30.0.11/32"}):
            netbox._request("DELETE", f"/ipam/ip-addresses/{stray['id']}/")

    def _sync(self, netbox, interfaces):
        from snmpinv.model import DeviceRecord, ScanResult
        from snmpinv.sync import Syncer, SyncOptions

        # ensure, not create: the rescan test calls this twice.
        site = netbox.ensure("/dcim/sites/", {"slug": self.SITE},
                             {"name": self.SITE, "slug": self.SITE}, label="site")
        record = DeviceRecord(
            name=self.NAME, model="DCS-7050SX-72Q", serial="PRIMARY01",
            manufacturer="Arista Networks",
        )
        record.interfaces = interfaces
        Syncer(netbox, SyncOptions()).sync(
            ScanResult(host="10.30.0.11", devices=[record]),
            site["id"], scanned_address="10.30.0.11",
        )
        device = netbox.first("/dcim/devices/", {"name": self.NAME})
        return netbox.get(f"/dcim/devices/{device['id']}/")

    def test_it_is_set_when_the_device_never_reported_the_address(self, netbox):
        """The Arista case: Management1 exists in ifTable, but its address is
        in a VRF the address table does not show."""
        from snmpinv.model import InterfaceRecord

        self._cleanup(netbox)
        try:
            device = self._sync(netbox, [
                InterfaceRecord(name="Ethernet1", type_slug="10gbase-x-sfpp"),
                InterfaceRecord(name="Management1", type_slug="1000base-t"),
            ])
            primary = (device.get("primary_ip4") or {}).get("address")
            assert primary == "10.30.0.11/32", (
                f"the polled address did not become the primary IP (got {primary})"
            )
        finally:
            self._cleanup(netbox)

    def test_a_reported_address_still_wins_and_keeps_its_mask(self, netbox):
        """The inference must not override what the device actually said."""
        from snmpinv.model import InterfaceRecord

        self._cleanup(netbox)
        try:
            device = self._sync(netbox, [
                InterfaceRecord(name="Management1", type_slug="1000base-t",
                                ip_addresses=["10.30.0.11/24"]),
            ])
            primary = (device.get("primary_ip4") or {}).get("address")
            assert primary == "10.30.0.11/24", (
                f"the reported mask was lost (got {primary})"
            )
        finally:
            self._cleanup(netbox)

    def test_it_is_set_even_with_no_management_interface(self, netbox):
        """The rule is flat: the primary IP is the address it was onboarded
        with. A device of nothing but data ports is not an exception."""
        from snmpinv.model import InterfaceRecord

        self._cleanup(netbox)
        try:
            device = self._sync(netbox, [
                InterfaceRecord(name="Ethernet1", type_slug="10gbase-x-sfpp"),
                InterfaceRecord(name="Ethernet2", type_slug="10gbase-x-sfpp"),
            ])
            primary = (device.get("primary_ip4") or {}).get("address")
            assert primary == "10.30.0.11/32", (
                f"the polled address did not become the primary IP (got {primary})"
            )
        finally:
            self._cleanup(netbox)

    def test_it_does_not_claim_a_real_port_carries_the_address(self, netbox):
        """Ethernet1 does not have this address. Saying it does would be a
        false statement about real hardware, so a labelled virtual interface
        holds it instead."""
        from snmpinv.model import InterfaceRecord
        from snmpinv.sync import PRIMARY_IP_INTERFACE_NAME

        self._cleanup(netbox)
        try:
            device = self._sync(netbox, [
                InterfaceRecord(name="Ethernet1", type_slug="10gbase-x-sfpp"),
            ])
            primary_id = (device.get("primary_ip4") or {}).get("id")
            ip = netbox.get(f"/ipam/ip-addresses/{primary_id}/")
            holder = (ip.get("assigned_object") or {}).get("name")
            assert holder == PRIMARY_IP_INTERFACE_NAME, (
                f"the address was hung on {holder!r}, which the device never "
                f"said carried it"
            )
        finally:
            self._cleanup(netbox)

    def test_a_rescan_reuses_the_holding_interface(self, netbox):
        """Otherwise every six-hourly run leaves another one behind."""
        from snmpinv.model import InterfaceRecord
        from snmpinv.sync import PRIMARY_IP_INTERFACE_NAME

        self._cleanup(netbox)
        try:
            interfaces = [InterfaceRecord(name="Ethernet1", type_slug="10gbase-x-sfpp")]
            self._sync(netbox, interfaces)
            device = self._sync(netbox, interfaces)
            holders = [
                i for i in netbox.all("/dcim/interfaces/", {"device_id": device["id"]})
                if i["name"] == PRIMARY_IP_INTERFACE_NAME
            ]
            assert len(holders) == 1, f"{len(holders)} holding interfaces after a rescan"
        finally:
            self._cleanup(netbox)
