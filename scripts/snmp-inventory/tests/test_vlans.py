"""VLANs: read from the device, filed by site, set on ports, linked to prefixes.

Two collection paths because Catalyst IOS only answers the bridge MIBs one
VLAN context at a time: Cisco's own MIBs on Cisco, Q-BRIDGE-MIB everywhere
else. One sync, where the device is the authority on what a port carries.
"""

from __future__ import annotations

import io

from conftest import ReplayCollector, fixture_path
from snmpinv import mibs, probe
from snmpinv.collect import PortVlans, _set_bits, _walk_cisco_vlans
from snmpinv.model import InterfaceRecord, build_scan_result, svi_vlan_id
from snmpinv.onboarding import scan_payload, scan_result_from_payload
from snmpinv.snmp import VarBind
from snmpinv.sync import SyncOptions, Syncer


def _facts(name: str):
    return ReplayCollector(fixture_path(name)).collect("192.0.2.1")


def _ports(facts) -> dict[str, PortVlans]:
    return {facts.interfaces[index].display_name(): port
            for index, port in facts.port_vlans.items()}


# --- collection -------------------------------------------------------------


def test_cisco_vlan_database_drops_the_token_ring_relics():
    facts = _facts("cisco-2960x")
    assert facts.vlan_source == "cisco"
    assert {v.vid: v.name for v in facts.vlans.values()} == {
        1: "default", 10: "USERS", 20: "VOICE", 99: "MGMT"}


def test_cisco_access_and_trunk_ports():
    ports = _ports(_facts("cisco-2960x"))
    assert ports["GigabitEthernet1/0/2"] == PortVlans(mode="access", untagged=10)
    assert ports["GigabitEthernet1/0/3"] == PortVlans(mode="access", untagged=20)
    # VLAN 30 is on the allowed list and not on the switch, so the trunk does
    # not carry it; 99 is the native VLAN, so it is untagged rather than tagged.
    assert ports["GigabitEthernet1/0/1"] == PortVlans(mode="tagged", untagged=99, tagged=(10, 20))
    assert "Vlan1" not in ports


class _Columns:
    """A session that serves whole columns from a dict of oid -> {row: VarBind}."""

    def __init__(self, columns):
        self.columns = columns

    def walk(self, host, oid):
        return list(self.columns.get(oid, {}).values())


def _hex(oid: str, data: bytes) -> VarBind:
    return VarBind(oid, "Hex-STRING", ":".join(f"{b:02X}" for b in data))


def _trunk_session(bitmaps: dict[str, bytes]) -> _Columns:
    columns = {
        mibs.CISCO_VTP_VLAN_NAME: {"1.1": VarBind(f"{mibs.CISCO_VTP_VLAN_NAME}.1.1", "STRING", "default"),
                                   "1.10": VarBind(f"{mibs.CISCO_VTP_VLAN_NAME}.1.10", "STRING", "USERS")},
        mibs.CISCO_TRUNK_DYNAMIC_STATUS: {"5": VarBind(f"{mibs.CISCO_TRUNK_DYNAMIC_STATUS}.5", "INTEGER", "1")},
        mibs.CISCO_TRUNK_NATIVE_VLAN: {"5": VarBind(f"{mibs.CISCO_TRUNK_NATIVE_VLAN}.5", "INTEGER", "1")},
    }
    for oid, data in bitmaps.items():
        columns[oid] = {"5": _hex(f"{oid}.5", data)}
    return _Columns(columns)


def test_a_trunk_allowing_every_vlan_is_tagged_all_not_the_whole_database():
    session = _trunk_session({oid: b"\xff" * 128 for oid in mibs.CISCO_TRUNK_VLAN_BITMAPS})
    _vlans, ports = _walk_cisco_vlans(session, "192.0.2.1")
    assert ports[5] == PortVlans(mode="tagged-all", untagged=1)


def test_tagged_all_holds_on_a_platform_that_serves_only_the_first_bitmap():
    session = _trunk_session({mibs.CISCO_TRUNK_VLANS_ENABLED: b"\xff" * 128})
    _vlans, ports = _walk_cisco_vlans(session, "192.0.2.1")
    assert ports[5].mode == "tagged-all"


def test_one_vlan_pruned_from_a_trunk_makes_it_an_explicit_list():
    first = bytearray(b"\xff" * 128)
    first[0] &= ~(0x80 >> 5) & 0xFF                 # VLAN 5 removed
    bitmaps = {oid: b"\xff" * 128 for oid in mibs.CISCO_TRUNK_VLAN_BITMAPS}
    bitmaps[mibs.CISCO_TRUNK_VLANS_ENABLED] = bytes(first)
    _vlans, ports = _walk_cisco_vlans(_trunk_session(bitmaps), "192.0.2.1")
    assert ports[5] == PortVlans(mode="tagged", untagged=1, tagged=(10,))


def test_a_cisco_router_with_no_vlan_database_costs_one_walk():
    asked = []

    class Router:
        def walk(self, host, oid):
            asked.append(oid)
            return []

    assert _walk_cisco_vlans(Router(), "192.0.2.1") == ({}, {})
    assert asked == [mibs.CISCO_VTP_VLAN_NAME]


def test_bits_are_numbered_from_the_most_significant():
    assert _set_bits(b"\x80\x01") == {0, 15}
    assert _set_bits(b"\x80\x01", first=1) == {1, 16}
    assert _set_bits(b"") == set()


def test_qbridge_ports_are_translated_from_bridge_port_to_interface():
    facts = _facts("arista-7050sx")
    assert facts.vlan_source == "q-bridge"
    assert {v.vid: v.name for v in facts.vlans.values()} == {
        1: "default", 100: "SERVERS", 200: "STORAGE"}
    ports = _ports(facts)
    assert ports["Ethernet1"] == PortVlans(mode="access", untagged=100)
    assert ports["Ethernet2"] == PortVlans(mode="tagged", untagged=1, tagged=(100, 200))
    assert ports["Port-Channel10"] == PortVlans(mode="tagged", untagged=None, tagged=(100, 200))


def test_a_bridge_port_in_no_vlan_is_not_an_access_port_in_its_pvid():
    ports = _ports(_facts("arista-7050sx"))
    assert "Ethernet3" not in ports and "Ethernet4" not in ports


def test_devices_with_no_vlan_tables_report_none():
    facts = _facts("palo-pa3220")
    assert (facts.vlans, facts.port_vlans, facts.vlan_source) == ({}, {}, "")
    assert build_scan_result(facts).vlans_reported is False


def test_collection_can_be_switched_off():
    facts = ReplayCollector(fixture_path("cisco-2960x"), collect_vlans=False).collect("192.0.2.1")
    assert not facts.vlans and not facts.port_vlans


# --- model ------------------------------------------------------------------


def test_svi_names_across_platforms():
    assert [svi_vlan_id(n) for n in ("Vlan100", "Vl10", "vlan20", "Vlan 30", "Vlan-interface40")] \
        == [100, 10, 20, 30, 40]
    assert [svi_vlan_id(n) for n in ("GigabitEthernet1/0/1", "Vlan5000", "Vlan0", "vlan.100",
                                     "Port-Channel10", "/Common/vlan100")] == [None] * 6


def test_port_vlans_reach_the_interface_records_and_survive_a_stored_preview():
    result = build_scan_result(_facts("cisco-2960x"))
    assert result.vlans_reported
    assert [(v.vid, v.name) for v in result.vlans][:2] == [(1, "default"), (10, "USERS")]
    by_name = {i.name: i for i in result.devices[0].interfaces}
    uplink = by_name["GigabitEthernet1/0/1"]
    assert (uplink.mode, uplink.untagged_vlan, uplink.tagged_vlans) == ("tagged", 99, [10, 20])

    rebuilt = scan_result_from_payload(scan_payload(result))
    again = {i.name: i for i in rebuilt.devices[0].interfaces}["GigabitEthernet1/0/1"]
    assert (again.mode, again.untagged_vlan, again.tagged_vlans) == ("tagged", 99, [10, 20])
    # A stored preview never clears anything: it cannot say what was routed.
    assert rebuilt.vlans_reported is False


def test_a_preview_stored_by_an_older_poller_still_loads():
    payload = scan_payload(build_scan_result(_facts("cisco-2960x")))
    for interface in payload["devices"][0]["interfaces"]:
        for key in ("mode", "untagged_vlan", "tagged_vlans"):
            del interface[key]
    port = scan_result_from_payload(payload).devices[0].interfaces[0]
    assert (port.mode, port.untagged_vlan, port.tagged_vlans) == ("", None, [])


# --- sync -------------------------------------------------------------------


class FakeNetBox:
    def __init__(self, vlans=(), prefixes=()):
        self.objects = {"/ipam/vlans/": list(vlans), "/ipam/prefixes/": list(prefixes)}
        self.created, self.updated, self.reads = [], [], []

    def _matches(self, item, params):
        for key, value in params.items():
            if key == "limit":
                continue
            field = {"site_id": "site", "vrf_id": "vrf"}.get(key, key)
            have = item.get(field)
            if value == "null":
                if have is not None:
                    return False
            elif str(have) != str(value):
                return False
        return True

    def all(self, path, params=None):
        self.reads.append((path, dict(params or {})))
        return [o for o in self.objects.get(path, []) if self._matches(o, params or {})]

    def first(self, path, params=None):
        found = self.all(path, params)
        return found[0] if found else None

    def create(self, path, payload, label=""):
        item = dict(payload, id=500 + len(self.created))
        self.objects.setdefault(path, []).append(item)
        self.created.append((path, payload))
        return item

    def update(self, path, object_id, payload, label=""):
        self.updated.append((path, object_id, payload))
        for item in self.objects.get(path, []):
            if item["id"] == object_id:
                item.update(payload)
                return item
        return None


DEVICE = {"id": 1, "name": "bld-b-acc-01", "site": {"id": 7}}


def _syncer(netbox, vlans=((10, "USERS"), (20, "VOICE"), (99, "MGMT")), reported=True):
    syncer = Syncer(netbox, SyncOptions())
    syncer._vlan_names = dict(vlans)
    syncer._vlans_reported = reported
    return syncer


def _interface(**values) -> dict:
    return {"id": 40, "name": "GigabitEthernet1/0/1", "mode": None,
            "untagged_vlan": None, "tagged_vlans": [], **values}


def test_vlans_are_created_at_the_devices_site_with_no_group():
    netbox = FakeNetBox()
    record = InterfaceRecord(name="GigabitEthernet1/0/2", type_slug="1000base-t",
                             mode="access", untagged_vlan=10)
    _syncer(netbox)._sync_interface_vlans(DEVICE, _interface(), record, tenant_id=3)
    created = [payload for path, payload in netbox.created if path == "/ipam/vlans/"]
    # The whole database, not only the VLAN this port uses.
    assert [(v["vid"], v["name"], v["site"], v["tenant"]) for v in created] == [
        (10, "USERS", 7, 3), (20, "VOICE", 7, 3), (99, "MGMT", 7, 3)]
    assert all("group" not in v for v in created)
    assert netbox.updated == [("/dcim/interfaces/", 40,
                               {"mode": "access", "untagged_vlan": 500, "tagged_vlans": []})]


def test_a_vlan_already_at_the_site_is_reused_whatever_group_it_is_in():
    netbox = FakeNetBox(vlans=[{"id": 11, "vid": 10, "name": "Users (floor 2)", "site": 7,
                                "group": {"id": 4}}])
    record = InterfaceRecord(name="x", type_slug="other", mode="access", untagged_vlan=10)
    _syncer(netbox, vlans=((10, "USERS"),))._sync_interface_vlans(DEVICE, _interface(), record, None)
    assert netbox.created == []
    # And its name is somebody's, not a placeholder, so it stays.
    assert netbox.updated == [("/dcim/interfaces/", 40,
                               {"mode": "access", "untagged_vlan": 11, "tagged_vlans": []})]


def test_the_same_vlan_id_at_another_site_is_a_different_vlan():
    netbox = FakeNetBox(vlans=[{"id": 11, "vid": 10, "name": "USERS", "site": 8}])
    vlan = _syncer(netbox)._site_vlan(7, 10)
    assert vlan["id"] != 11 and vlan["site"] == 7


def test_a_placeholder_name_gives_way_to_a_real_one_and_never_the_reverse():
    netbox = FakeNetBox(vlans=[{"id": 11, "vid": 10, "name": "VLAN0010", "site": 7},
                               {"id": 12, "vid": 20, "name": "Phones", "site": 7}])
    syncer = _syncer(netbox)
    assert syncer._site_vlan(7, 10)["name"] == "USERS"
    assert syncer._site_vlan(7, 20)["name"] == "Phones"
    assert netbox.updated == [("/ipam/vlans/", 11, {"name": "USERS"})]


def test_an_unnamed_vlan_gets_the_switch_default_name():
    assert _syncer(FakeNetBox(), vlans=())._site_vlan(7, 30)["name"] == "VLAN0030"


def test_site_vlans_are_read_once_per_site_not_once_per_vlan():
    netbox = FakeNetBox()
    syncer = _syncer(netbox)
    for vid in (10, 20, 99, 10):
        syncer._site_vlan(7, vid)
    assert [r for r in netbox.reads if r[0] == "/ipam/vlans/"] == [("/ipam/vlans/", {"site_id": 7})]


def test_a_trunk_is_written_and_a_second_pass_writes_nothing():
    netbox = FakeNetBox()
    syncer = _syncer(netbox)
    record = InterfaceRecord(name="GigabitEthernet1/0/1", type_slug="1000base-t",
                             mode="tagged", untagged_vlan=99, tagged_vlans=[10, 20])
    interface = _interface()
    syncer._sync_interface_vlans(DEVICE, interface, record, None)
    ids = {v["vid"]: v["id"] for v in netbox.objects["/ipam/vlans/"]}
    assert netbox.updated == [("/dcim/interfaces/", 40, {
        "mode": "tagged", "untagged_vlan": ids[99], "tagged_vlans": sorted([ids[10], ids[20]])})]

    # As NetBox hands it back: a choice dict and nested VLANs.
    interface = _interface(mode={"value": "tagged", "label": "Tagged"},
                           untagged_vlan={"id": ids[99], "vid": 99},
                           tagged_vlans=[{"id": ids[20], "vid": 20}, {"id": ids[10], "vid": 10}])
    netbox.updated.clear()
    syncer._sync_interface_vlans(DEVICE, interface, record, None)
    assert netbox.updated == []


def test_tagged_all_carries_a_native_vlan_and_no_list():
    netbox = FakeNetBox()
    record = InterfaceRecord(name="x", type_slug="other", mode="tagged-all", untagged_vlan=99,
                             tagged_vlans=[])
    _syncer(netbox)._sync_interface_vlans(DEVICE, _interface(), record, None)
    assert netbox.updated[-1][2]["mode"] == "tagged-all"
    assert netbox.updated[-1][2]["tagged_vlans"] == []


def test_the_device_wins_over_what_netbox_held():
    netbox = FakeNetBox(vlans=[{"id": 11, "vid": 10, "name": "USERS", "site": 7},
                               {"id": 12, "vid": 20, "name": "VOICE", "site": 7}])
    record = InterfaceRecord(name="x", type_slug="other", mode="access", untagged_vlan=20)
    interface = _interface(mode={"value": "access"}, untagged_vlan={"id": 11, "vid": 10})
    _syncer(netbox, vlans=((10, "USERS"), (20, "VOICE")))._sync_interface_vlans(
        DEVICE, interface, record, None)
    assert netbox.updated == [("/dcim/interfaces/", 40,
                               {"mode": "access", "untagged_vlan": 12, "tagged_vlans": []})]


def test_a_port_the_switch_calls_routed_is_cleared_all_three_fields_at_once():
    netbox = FakeNetBox()
    record = InterfaceRecord(name="x", type_slug="other")
    interface = _interface(mode={"value": "tagged"}, untagged_vlan={"id": 11},
                           tagged_vlans=[{"id": 12}])
    _syncer(netbox, vlans=())._sync_interface_vlans(DEVICE, interface, record, None)
    assert netbox.updated == [("/dcim/interfaces/", 40,
                               {"mode": None, "untagged_vlan": None, "tagged_vlans": []})]


def test_a_device_that_reported_no_port_vlans_clears_nothing():
    netbox = FakeNetBox()
    record = InterfaceRecord(name="x", type_slug="other")
    interface = _interface(mode={"value": "access"}, untagged_vlan={"id": 11})
    _syncer(netbox, vlans=(), reported=False)._sync_interface_vlans(DEVICE, interface, record, None)
    assert netbox.updated == []


def _svi(*addresses) -> InterfaceRecord:
    return InterfaceRecord(name="Vlan100", type_slug="virtual", ip_addresses=list(addresses))


def test_the_prefix_behind_an_svi_is_linked_to_its_vlan():
    netbox = FakeNetBox(prefixes=[{"id": 70, "prefix": "10.30.0.0/16", "vrf": None, "vlan": None},
                                  {"id": 71, "prefix": "10.30.100.0/24", "vrf": None, "vlan": None}])
    _syncer(netbox, vlans=((100, "SERVERS"),), reported=False)._sync_interface_vlans(
        DEVICE, _interface(name="Vlan100"), _svi("10.30.100.2/24"), None)
    vlan_id = netbox.objects["/ipam/vlans/"][0]["id"]
    # The /24 the address is in, never the /16 that happens to contain it.
    assert netbox.updated == [("/ipam/prefixes/", 71, {"vlan": vlan_id})]


def test_the_prefix_is_looked_up_in_the_hosts_own_routing_table():
    netbox = FakeNetBox(prefixes=[{"id": 70, "prefix": "10.30.100.0/24", "vrf": None, "vlan": None},
                                  {"id": 71, "prefix": "10.30.100.0/24", "vrf": 9, "vlan": None}])
    syncer = _syncer(netbox, vlans=((100, "SERVERS"),), reported=False)
    syncer._vrf_id = 9
    syncer._sync_interface_vlans(DEVICE, _interface(name="Vlan100"), _svi("10.30.100.2/24"), None)
    assert [u[1] for u in netbox.updated] == [71]


def test_a_prefix_on_a_different_vlan_follows_the_device():
    netbox = FakeNetBox(vlans=[{"id": 11, "vid": 100, "name": "SERVERS", "site": 7}],
                        prefixes=[{"id": 71, "prefix": "10.30.100.0/24", "vrf": None,
                                   "vlan": {"id": 99, "vid": 300}}])
    _syncer(netbox, vlans=(), reported=False)._sync_interface_vlans(
        DEVICE, _interface(name="Vlan100"), _svi("10.30.100.2/24"), None)
    assert netbox.updated == [("/ipam/prefixes/", 71, {"vlan": 11})]


def test_a_stretched_vlan_is_not_traded_between_its_two_sites():
    netbox = FakeNetBox(vlans=[{"id": 11, "vid": 100, "name": "SERVERS", "site": 7}],
                        prefixes=[{"id": 71, "prefix": "10.30.100.0/24", "vrf": None,
                                   "vlan": {"id": 44, "vid": 100}}])      # site 8's VLAN 100
    _syncer(netbox, vlans=(), reported=False)._sync_interface_vlans(
        DEVICE, _interface(name="Vlan100"), _svi("10.30.100.2/24"), None)
    assert netbox.updated == []


def test_host_routes_link_local_and_missing_prefixes_are_left_alone():
    netbox = FakeNetBox(prefixes=[{"id": 70, "prefix": "10.30.100.2/32", "vrf": None, "vlan": None}])
    _syncer(netbox, vlans=(), reported=False)._sync_interface_vlans(
        DEVICE, _interface(name="Vlan100"),
        _svi("10.30.100.2/32", "fe80::1/64", "10.99.0.1/24"), None)
    assert netbox.updated == []
    assert [p for p, _ in netbox.created] == ["/ipam/vlans/"]      # the VLAN, never a prefix


def _sync_whole_device(sync_vlans: bool) -> FakeNetBox:
    """The 2960X through the real interface loop, addresses and MACs aside."""
    netbox = FakeNetBox()
    netbox.ensure_fields = lambda path, existing, desired, label="": existing
    syncer = Syncer(netbox, SyncOptions(sync_vlans=sync_vlans, sync_ips=False,
                                        set_primary_ip=False))
    syncer._ensure_mac = lambda interface, mac: None
    result = build_scan_result(_facts("cisco-2960x"))
    netbox.objects["/dcim/interfaces/"] = [
        {"id": 40 + n, "device_id": 1, "name": i.name, "enabled": i.enabled}
        for n, i in enumerate(result.devices[0].interfaces)]
    syncer._vlan_names = {v.vid: v.name for v in result.vlans}
    syncer._vlans_reported = result.vlans_reported
    syncer._sync_interfaces(DEVICE, result.devices[0], "")
    return netbox


def test_a_whole_switch_lands_with_its_vlans_and_port_modes():
    netbox = _sync_whole_device(sync_vlans=True)
    assert sorted(v["vid"] for v in netbox.objects["/ipam/vlans/"]) == [1, 10, 20, 99]
    modes = {i["name"]: i.get("mode") for i in netbox.objects["/dcim/interfaces/"]}
    assert modes == {"GigabitEthernet1/0/1": "tagged", "GigabitEthernet1/0/2": "access",
                     "GigabitEthernet1/0/3": "access", "Vlan1": None}


def test_sync_vlans_off_touches_nothing():
    netbox = _sync_whole_device(sync_vlans=False)
    assert netbox.created == [] and netbox.updated == []


# --- probe ------------------------------------------------------------------


def test_probe_prints_the_vlans_and_what_would_be_linked():
    facts = _facts("arista-7050sx")
    out = io.StringIO()
    probe._print_report(facts, build_scan_result(facts), out)
    text = out.getvalue()
    assert "VLANS (3, from Q-BRIDGE-MIB)" in text
    assert "access vlan 100" in text
    assert "trunk 100,200, native 1" in text
    assert "Vlan100: the prefix of 10.30.100.2/24 would be linked to VLAN 100" in text
    as_json = probe.facts_to_dict(facts, build_scan_result(facts))
    assert as_json["vlans"]["source"] == "q-bridge"
    assert {"mode": "access", "untagged": 100, "tagged": []} in [
        i["vlans"] for i in as_json["interfaces"]]


def test_probe_ranges_read_like_a_switch():
    assert probe._ranges([1, 2, 3, 10, 20, 21]) == "1-3,10,20-21"
