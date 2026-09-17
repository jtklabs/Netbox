"""Overlapping address space: one address, two networks, two routing tables.

10.10.1.5 is our switch in the global table and an acquired company's firewall
in their VRF. Everything the scanner does with an address -- choosing targets,
matching a scan to a record, writing the address back -- has to stay inside
the routing table the host was found in. Looking across all of them finds the
other network's copy: the sync declines to steal it and the device lands with
no address, or a device with no serial is matched to the wrong record by it.
"""

from __future__ import annotations

import logging

from test_contexts import FakeNetBox, _id, chassis_in, syncer
from test_selection import OURS, THEIRS, FakeNetBox as SelectionNetBox, device, tags

import snmp_inventory
from snmpinv import onboarding
from snmpinv.model import DeviceRecord, InterfaceRecord, ScanResult
from snmpinv.selection import select_targets

ACQ = 7          # the acquired company's VRF
ADDRESS = "10.10.1.5"


def scan(name, serial="", address=ADDRESS, host=ADDRESS, interface="mgmt"):
    return ScanResult(host=host, devices=[DeviceRecord(
        name=name, serial=serial, model="FG-600E", manufacturer="Fortinet",
        interfaces=[InterfaceRecord(name=interface, type_slug="1000base-t",
                                    ip_addresses=[f"{address}/24"])],
    )])


def ips(netbox):
    return netbox.objects.get("/ipam/ip-addresses/", [])


class TestWritingAddresses:
    def test_the_same_address_in_another_table_is_not_in_the_way(self):
        """Before: found ours, 'already assigned elsewhere', no address at all."""
        netbox = FakeNetBox()
        ours = chassis_in(netbox, name="our-switch", address=ADDRESS, serial="OURS1")
        syncer(netbox).sync(scan("acq-fw", serial="THEIRS1"), site_id=2,
                            scanned_address=ADDRESS, vrf_id=ACQ)

        theirs = netbox.device("acq-fw")
        mine = [ip for ip in ips(netbox) if _id(ip.get("vrf")) == ACQ]
        assert len(mine) == 1 and mine[0]["address"] == f"{ADDRESS}/24"
        assert _id(theirs["primary_ip4"]) == mine[0]["id"]
        # And ours is exactly as it was.
        assert _id(netbox.device("our-switch")["primary_ip4"]) == _id(ours["primary_ip4"])
        assert len(ips(netbox)) == 2

    def test_no_vrf_means_the_global_table_and_a_vrf_copy_is_left_alone(self):
        netbox = FakeNetBox()
        theirs = netbox.add("/ipam/ip-addresses/", address=f"{ADDRESS}/24", vrf=ACQ)
        syncer(netbox).sync(scan("our-switch", serial="OURS1"), site_id=1,
                            scanned_address=ADDRESS)
        created = [ip for ip in ips(netbox) if ip["id"] != theirs["id"]]
        assert len(created) == 1 and created[0].get("vrf") is None
        assert theirs.get("assigned_object_id") is None        # not adopted

    def test_an_unassigned_address_is_adopted_only_from_the_same_table(self):
        netbox = FakeNetBox()
        netbox.add("/ipam/ip-addresses/", address=f"{ADDRESS}/24")             # global, imported
        imported = netbox.add("/ipam/ip-addresses/", address=f"{ADDRESS}/24", vrf=ACQ)
        syncer(netbox).sync(scan("acq-fw", serial="THEIRS1"), site_id=2,
                            scanned_address=ADDRESS, vrf_id=ACQ)
        assert imported.get("assigned_object_id") is not None
        assert len(ips(netbox)) == 2                                          # nothing new

    def test_an_address_written_before_vrfs_were_known_is_moved_into_its_vrf(self):
        netbox = FakeNetBox()
        chassis_in(netbox, name="acq-fw", address=ADDRESS, serial="THEIRS1")   # on mgmt0
        assert ips(netbox)[0].get("vrf") is None
        syncer(netbox).sync(scan("acq-fw", serial="THEIRS1", interface="mgmt0"), site_id=1,
                            scanned_address=ADDRESS, vrf_id=ACQ)
        assert [_id(ip.get("vrf")) for ip in ips(netbox)] == [ACQ]

    def test_the_vrf_does_not_leak_into_the_next_host(self):
        netbox = FakeNetBox()
        worker = syncer(netbox)
        worker.sync(scan("acq-fw", serial="THEIRS1"), site_id=2,
                    scanned_address=ADDRESS, vrf_id=ACQ)
        worker.sync(scan("our-switch", serial="OURS1", address="10.20.0.9", host="10.20.0.9"),
                    site_id=1, scanned_address="10.20.0.9")
        ours = next(ip for ip in ips(netbox) if ip["address"].startswith("10.20.0.9"))
        assert ours.get("vrf") is None
        assert worker._in_vrf() == {"vrf_id": "null"}


class TestMatchingAScanToARecord:
    def test_a_device_with_no_serial_is_not_matched_across_tables_by_its_address(self):
        """The overwrite, in its overlapping-space form: two boxes with no
        serial at the same address in two networks."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="our-printer", address=ADDRESS, serial="")
        syncer(netbox).sync(scan("their-printer"), site_id=2,
                            scanned_address=ADDRESS, vrf_id=ACQ)
        assert sorted(d["name"] for d in netbox.devices()) == ["our-printer", "their-printer"]

    def test_within_one_table_the_address_still_finds_the_record(self):
        netbox = FakeNetBox()
        record = chassis_in(netbox, name="their-printer", address=ADDRESS, serial="")
        netbox.objects["/ipam/ip-addresses/"][0]["vrf"] = {"id": ACQ}
        found = None
        worker = syncer(netbox)
        worker._vrf_id = ACQ
        found = worker._find_device(DeviceRecord(name="renamed"), 2, ADDRESS)
        assert found["id"] == record["id"]


# --- the sweep ---------------------------------------------------------------


class VrfSelectionNetBox(SelectionNetBox):
    """The selection fake, honouring the two filters VRFs add."""

    def all(self, path, params=None):
        params = dict(params or {})
        if path == "/ipam/ip-addresses/" and "id" in params:
            self.queries.append((path, params))
            return [ip for ip in self.ip_addresses if ip.get("id") in params["id"]]
        vrf = params.pop("vrf_id", None) if path == "/ipam/ip-addresses/" else None
        out = super().all(path, params)
        if vrf is not None:
            self.queries[-1] = (path, dict(params, vrf_id=vrf))
            wanted = None if vrf == "null" else vrf
            out = [ip for ip in out if (ip.get("vrf") or {}).get("id") == wanted]
        return out


def sweep(prefixes, addresses, devices=(), tag_slugs=(OURS,)):
    netbox = VrfSelectionNetBox(
        regions=[], devices=list(devices), prefixes=prefixes, ip_addresses=addresses,
        sites=[{"id": 10, "name": "hq", "region": None, "tags": tags(OURS)},
               {"id": 20, "name": "acq", "region": None, "tags": tags(OURS)}],
        tag_slugs=tag_slugs,
    )
    return netbox, select_targets(netbox, "boston")


class TestChoosingTargets:
    def test_a_prefix_only_yields_the_addresses_in_its_own_table(self):
        """`parent` is a containment test. Without the VRF, our global /24
        pulled in every 10.10.1.x held in another company's VRF."""
        netbox, targets = sweep(
            prefixes=[{"prefix": "10.10.1.0/24", "vrf": None, "_site_id": 10}],
            addresses=[
                {"id": 1, "address": "10.10.1.5/24", "vrf": None, "_prefix": "10.10.1.0/24"},
                {"id": 2, "address": "10.10.1.9/24", "vrf": {"id": ACQ}, "_prefix": "10.10.1.0/24"},
            ])
        assert [(t.address, t.vrf_id) for t in targets] == [("10.10.1.5", None)]
        asked = [q for path, q in netbox.queries if path == "/ipam/ip-addresses/" and "parent" in q]
        assert asked == [{"parent": "10.10.1.0/24", "vrf_id": "null"}]

    def test_a_vrf_prefix_carries_its_vrf_onto_the_target(self):
        _netbox, targets = sweep(
            prefixes=[{"prefix": "10.10.1.0/24", "vrf": {"id": ACQ}, "_site_id": 20}],
            addresses=[
                {"id": 1, "address": "10.10.1.5/24", "vrf": None, "_prefix": "10.10.1.0/24"},
                {"id": 2, "address": "10.10.1.9/24", "vrf": {"id": ACQ}, "_prefix": "10.10.1.0/24"},
            ])
        assert [(t.address, t.vrf_id) for t in targets] == [("10.10.1.9", ACQ)]

    def test_a_known_device_is_rescanned_in_the_table_its_address_is_in(self):
        _netbox, targets = sweep(
            prefixes=[], devices=[device(4, "acq-fw", 20, "10.10.1.5/24")],
            addresses=[{"id": 400, "address": "10.10.1.5/24", "vrf": {"id": ACQ},
                        "_prefix": None}])
        assert [(t.address, t.vrf_id, t.source) for t in targets] == [
            ("10.10.1.5", ACQ, "device-primary-ip")]

    def test_one_address_in_two_tables_is_scanned_once_and_said_out_loud(self, caplog):
        with caplog.at_level(logging.WARNING):
            _netbox, targets = sweep(
                prefixes=[{"prefix": "10.10.1.0/24", "vrf": None, "_site_id": 10},
                          {"prefix": "10.10.1.0/24", "vrf": {"id": ACQ}, "_site_id": 20}],
                addresses=[
                    {"id": 1, "address": "10.10.1.5/24", "vrf": None, "_prefix": "10.10.1.0/24"},
                    {"id": 2, "address": "10.10.1.5/24", "vrf": {"id": ACQ},
                     "_prefix": "10.10.1.0/24"},
                ])
        assert [(t.address, t.vrf_id) for t in targets] == [("10.10.1.5", None)]
        assert "is a target in two routing tables" in caplog.text

    def test_another_pollers_device_in_a_vrf_does_not_knock_out_ours(self):
        """It claims 10.10.1.5 in ITS table. Ours, in the global table, is
        a different device, and used to vanish from the sweep."""
        _netbox, targets = sweep(
            prefixes=[{"prefix": "10.10.1.0/24", "vrf": None, "_site_id": 10}],
            addresses=[
                {"id": 1, "address": "10.10.1.5/24", "vrf": None, "_prefix": "10.10.1.0/24"},
                {"id": 900, "address": "10.10.1.5/24", "vrf": {"id": ACQ}, "_prefix": None},
            ],
            devices=[device(9, "acq-fw", 99, "10.10.1.5/24", (THEIRS,))],
            tag_slugs=(OURS, THEIRS),
        )
        assert [(t.address, t.vrf_id) for t in targets] == [("10.10.1.5", None)]

    def test_but_a_claim_in_the_same_table_still_excludes_it(self):
        _netbox, targets = sweep(
            prefixes=[{"prefix": "10.10.1.0/24", "vrf": None, "_site_id": 10}],
            addresses=[
                {"id": 1, "address": "10.10.1.5/24", "vrf": None, "_prefix": "10.10.1.0/24"},
                {"id": 900, "address": "10.10.1.5/24", "vrf": None, "_prefix": None},
            ],
            devices=[device(9, "their-sw", 99, "10.10.1.5/24", (THEIRS,))],
            tag_slugs=(OURS, THEIRS),
        )
        assert targets == []


# --- onboarding and the command line ------------------------------------------


class RecordingSyncer:
    def __init__(self):
        self.calls = []

    def sync(self, result, site_id, scanned_address="", tenant_id=None, vrf_id=None):
        self.calls.append({"site": site_id, "tenant": tenant_id, "vrf": vrf_id})

    def flush_software_reports(self):
        pass


class Reporting:
    def first(self, path, params=None):
        return {"id": 55, "name": "acq-fw"}

    def post_raw(self, path, payload, label=""):
        return {}


class TestOnboarding:
    def test_an_approved_job_is_written_into_the_requests_vrf(self):
        worker = RecordingSyncer()
        onboarding._write_and_report(Reporting(), worker, 1, ADDRESS, scan("acq-fw"),
                                     site_id=2, tenant_id=3, vrf_id=ACQ)
        assert worker.calls == [{"site": 2, "tenant": 3, "vrf": ACQ}]

    def test_a_scan_the_server_approved_on_the_spot_is_too(self):
        worker = RecordingSyncer()
        request = {"id": 1, "site": {"id": 2}, "tenant": {"id": 3}, "vrf": {"id": ACQ}}
        onboarding._apply_result(Reporting(), worker, request, scan("acq-fw"), ADDRESS, False)
        assert worker.calls == [{"site": 2, "tenant": 3, "vrf": ACQ}]

    def test_no_vrf_on_the_request_is_the_global_table(self):
        worker = RecordingSyncer()
        request = {"id": 1, "site": {"id": 2}, "tenant": None, "vrf": None}
        onboarding._apply_result(Reporting(), worker, request, scan("sw"), ADDRESS, False)
        assert worker.calls == [{"site": 2, "tenant": None, "vrf": None}]


class TestTheCommandLine:
    class Prefixes:
        def __init__(self):
            self.asked = []

        def all(self, path, params=None):
            self.asked.append(params)
            return [{"prefix": "10.10.1.0/24", "scope_type": "dcim.site", "scope": {"id": 20}}]

    def test_a_manual_host_scan_uses_the_global_table_unless_told(self):
        netbox = self.Prefixes()
        assert snmp_inventory._site_from_prefix(netbox, ADDRESS) == 20
        assert snmp_inventory._site_from_prefix(netbox, ADDRESS, ACQ) == 20
        assert netbox.asked == [{"contains": ADDRESS, "vrf_id": "null"},
                                {"contains": ADDRESS, "vrf_id": ACQ}]

    def test_vrf_id_is_offered(self):
        args = snmp_inventory.parse_args(["--host", ADDRESS, "--vrf-id", str(ACQ)])
        assert args.vrf_id == ACQ
        assert snmp_inventory.parse_args(["--host", ADDRESS]).vrf_id is None
