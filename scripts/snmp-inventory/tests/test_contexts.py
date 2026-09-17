"""One chassis, several management addresses: Nexus VDCs and F5 vCMP guests.

Each VDC or guest answers on its own address and reports the chassis serial as
its own. Taken at face value that is two devices with one serial, which the
sync refuses -- and did, so neither ever landed. These pin what the scanner
makes of them instead, and that a serial sitting on two NetBox records can no
longer pull a scan onto the wrong one.

The walks are synthetic: no fixture has been captured from a partitioned
Nexus or a vCMP host yet. When one is (--save-walk), it replaces the walk
built here and the assertions should hold unchanged.
"""

from __future__ import annotations

import logging

import pytest
from conftest import ReplayCollector

from snmpinv import sync as sync_module
from snmpinv.collect import VdcRow
from snmpinv.model import ContextRecord, DeviceRecord, ScanResult, build_scan_result
from snmpinv.netbox import NetBoxError
from snmpinv.onboarding import _find_created_device, scan_payload, scan_result_from_payload
from snmpinv.sync import MANUAL_TAG, VCMP_HOST_FIELD, VDC_ENDPOINT, SyncOptions, Syncer

CHASSIS_SERIAL = "JAF1234ABCD"
HOST_SERIAL = "chs123456s"


# --- synthetic walks --------------------------------------------------------


def _system(descr, oid, name):
    return [
        f'.1.3.6.1.2.1.1.1.0 = STRING: "{descr}"',
        f'.1.3.6.1.2.1.1.2.0 = OID: {oid}',
        '.1.3.6.1.2.1.1.3.0 = Timeticks: (123456700) 0:00:00.00',
        f'.1.3.6.1.2.1.1.5.0 = STRING: "{name}"',
    ]


def _chassis_entity(index, model, serial, mfg="Cisco Systems, Inc."):
    e = f".1.3.6.1.2.1.47.1.1.1.1"
    return [
        f'{e}.2.{index} = STRING: "{model} Chassis"',
        f'{e}.4.{index} = INTEGER: 0',
        f'{e}.5.{index} = INTEGER: 3',
        f'{e}.6.{index} = INTEGER: -1',
        f'{e}.7.{index} = STRING: "Chassis"',
        f'{e}.11.{index} = STRING: "{serial}"',
        f'{e}.12.{index} = STRING: "{mfg}"',
        f'{e}.13.{index} = STRING: "{model}"',
    ]


def _interface(index, name, if_type=6, speed=1000, address=None, prefix=24):
    rows = [
        f'.1.3.6.1.2.1.2.2.1.1.{index} = INTEGER: {index}',
        f'.1.3.6.1.2.1.2.2.1.2.{index} = STRING: "{name}"',
        f'.1.3.6.1.2.1.2.2.1.3.{index} = INTEGER: {if_type}',
        f'.1.3.6.1.2.1.2.2.1.7.{index} = INTEGER: 1',
        f'.1.3.6.1.2.1.2.2.1.8.{index} = INTEGER: 1',
        f'.1.3.6.1.2.1.31.1.1.1.1.{index} = STRING: "{name}"',
        f'.1.3.6.1.2.1.31.1.1.1.15.{index} = Gauge32: {speed}',
    ]
    if address:
        net = ".".join(address.split(".")[:3]) + ".0"
        rows += [
            f'.1.3.6.1.2.1.4.34.1.3.1.4.{address} = INTEGER: {index}',
            f'.1.3.6.1.2.1.4.34.1.4.1.4.{address} = INTEGER: 1',
            f'.1.3.6.1.2.1.4.34.1.5.1.4.{address} = OID: 1.3.6.1.2.1.4.32.1.5.{index}.1.4.{net}.{prefix}',
            f'.1.3.6.1.2.1.4.34.1.6.1.4.{address} = INTEGER: 2',
            f'.1.3.6.1.2.1.4.34.1.7.1.4.{address} = INTEGER: 1',
        ]
    return rows


def _vdc_rows(rows):
    out = []
    for vdc_id, name in rows:
        out.append(f'.1.3.6.1.4.1.9.9.774.1.1.1.2.{vdc_id} = STRING: "{name}"')
        out.append(f'.1.3.6.1.4.1.9.9.774.1.1.1.3.{vdc_id} = INTEGER: 1')
    return out


NXOS_DESCR = ("Cisco NX-OS(tm) n7000, Software (n7000-s2-dk9), Version 8.4(4), "
              "RELEASE SOFTWARE Copyright (c) 2002-2020 by Cisco Systems, Inc.")


def nexus_walk(tmp_path, sys_name, vdcs, address="10.0.0.12", filename="nexus.walk"):
    lines = (_system(NXOS_DESCR, "1.3.6.1.4.1.9.12.3.1.3.1330", sys_name)
             + _chassis_entity(149, "N7K-C7010", CHASSIS_SERIAL)
             + _interface(83886080, "mgmt0", address=address)
             + _interface(436211200, "Ethernet3/1", speed=10000)
             + _vdc_rows(vdcs))
    path = tmp_path / filename
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def f5_walk(tmp_path, sys_name, platform_id, marketing, address="10.0.0.20", filename="f5.walk"):
    lines = (_system("Linux ltm 3.10.0 BIG-IP", "1.3.6.1.4.1.3375.2.1.3.4.20", sys_name)
             + [
                 f'.1.3.6.1.4.1.3375.2.1.4.2.0 = STRING: "17.1.1.3"',
                 f'.1.3.6.1.4.1.3375.2.1.4.3.0 = STRING: "0.0.5"',
                 f'.1.3.6.1.4.1.3375.2.1.3.3.3.0 = STRING: "{HOST_SERIAL}"',
                 f'.1.3.6.1.4.1.3375.2.1.3.5.1.0 = STRING: "{platform_id}"',
                 f'.1.3.6.1.4.1.3375.2.1.3.5.2.0 = STRING: "{marketing}"',
             ]
             + _interface(1, "mgmt", address=address))
    path = tmp_path / filename
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def scan(path, address):
    return build_scan_result(ReplayCollector(path).collect(address))


# --- what the scanner makes of them -----------------------------------------


class TestANexusVdc:
    def test_a_non_default_vdc_is_a_partition_of_the_chassis(self, tmp_path):
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        device = result.primary
        assert device.context is not None and device.context.is_vdc
        assert device.context.identifier == 2
        assert device.context.name == "dmz"
        # The serial is kept: it is how the chassis is found.
        assert device.serial == CHASSIS_SERIAL
        assert device.context.chassis_serial == CHASSIS_SERIAL
        assert device.platform == "Cisco NX-OS"
        assert device.model == "N7K-C7010"

    def test_the_default_vdc_is_the_chassis(self, tmp_path):
        result = scan(nexus_walk(tmp_path, "n7k-1", [(1, "n7k-1"), (2, "dmz"), (3, "prod")]),
                      "10.0.0.11")
        assert result.primary.context is None
        assert result.primary.serial == CHASSIS_SERIAL

    def test_a_nexus_without_the_table_is_a_chassis(self, tmp_path):
        result = scan(nexus_walk(tmp_path, "n9k-1", []), "10.0.0.13")
        assert result.primary.context is None

    def test_an_unpartitioned_nexus_lists_only_the_default_vdc(self, tmp_path):
        result = scan(nexus_walk(tmp_path, "n7k-2", [(1, "n7k-2")]), "10.0.0.14")
        assert result.primary.context is None

    def test_the_table_is_only_asked_of_nxos(self, tmp_path):
        path = nexus_walk(tmp_path, "sw-1", [(2, "dmz")])
        text = open(path).read().replace(NXOS_DESCR, "Cisco IOS Software, Version 15.2(7)E")
        open(path, "w").write(text)
        collector = ReplayCollector(path)
        collector.collect("10.0.0.15")
        assert "1.3.6.1.4.1.9.9.774.1.1.1" not in collector.session.walk_calls

    def test_the_probe_shows_the_table_and_the_verdict(self, tmp_path):
        import io
        from snmpinv import probe as probe_module
        facts = ReplayCollector(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")])).collect("10.0.0.12")
        out = io.StringIO()
        probe_module._print_report(facts, build_scan_result(facts), out)
        text = out.getvalue()
        assert "VIRTUAL DEVICE CONTEXTS" in text and "dmz" in text
        assert "answered as VDC 2" in text
        assert "virtual device context 'dmz'" in text
        as_json = probe_module.facts_to_dict(facts, build_scan_result(facts))
        assert as_json["vdcs"] == [{"id": 2, "name": "dmz", "state": "active"}]
        assert as_json["identification"]["context"]["kind"] == "vdc"


class TestWhichVdcAnswered:
    """Should an agent list every VDC, the hostname says which one it is."""

    def facts_with(self, sys_name, rows):
        from snmpinv.collect import DeviceFacts
        facts = DeviceFacts(host="10.0.0.1")
        facts.sys_name = sys_name
        facts.vdcs = [VdcRow(vdc_id=i, name=n) for i, n in rows]
        return facts

    def test_one_row_is_that_row(self):
        from snmpinv.model import _own_vdc
        assert _own_vdc(self.facts_with("anything", [(3, "prod")])).vdc_id == 3

    def test_a_combined_hostname_names_its_vdc(self):
        from snmpinv.model import _own_vdc
        rows = [(1, "n7k-1"), (2, "dmz"), (3, "dmz-b")]
        assert _own_vdc(self.facts_with("n7k-1-dmz", rows)).vdc_id == 2
        assert _own_vdc(self.facts_with("n7k-1-dmz-b", rows)).vdc_id == 3
        assert _own_vdc(self.facts_with("N7K-1.example.net", rows)).vdc_id == 1

    def test_no_clue_means_the_default_vdc(self):
        from snmpinv.model import _own_vdc
        assert _own_vdc(self.facts_with("something-else", [(1, "a"), (2, "b")])).vdc_id == 1


class TestAVcmpGuest:
    def test_a_guest_is_a_device_without_the_hosts_serial(self, tmp_path):
        result = scan(f5_walk(tmp_path, "ltm-guest-01", "Z101", "BIG-IP vCMP Guest"), "10.0.0.20")
        device = result.primary
        assert device.context is not None and device.context.is_vcmp_guest
        assert device.serial == ""
        assert device.context.chassis_serial == HOST_SERIAL
        # The model is whatever the guest reports for itself, verbatim.
        assert device.model == "BIG-IP vCMP Guest"
        assert device.software_version == "17.1.1.3-0.0.5"
        assert device.platform == "F5 TMOS"

    def test_a_host_is_an_ordinary_bigip(self, tmp_path):
        result = scan(f5_walk(tmp_path, "vcmp-host-01", "C113", "BIG-IP 4000"), "10.0.0.21")
        assert result.primary.context is None
        assert result.primary.serial == HOST_SERIAL

    def test_the_probe_says_so(self, tmp_path):
        import io
        from snmpinv import probe as probe_module
        facts = ReplayCollector(f5_walk(tmp_path, "ltm-guest-01", "Z101", "BIG-IP vCMP Guest")).collect("10.0.0.20")
        out = io.StringIO()
        probe_module._print_report(facts, build_scan_result(facts), out)
        text = out.getvalue()
        assert "Z101 (vCMP guest)" in text
        assert "vCMP guest: linked to the host with chassis serial chs123456s" in text


# --- what the sync does with them --------------------------------------------

FK_FIELDS = ("device", "site", "role", "device_type", "platform", "manufacturer",
             "tenant", "virtual_chassis", "primary_ip4", "vrf")


def _id(value):
    return value.get("id") if isinstance(value, dict) else value


class FakeNetBox:
    """In-memory NetBox: enough of the REST shapes the sync reads back."""

    dry_run = False

    def __init__(self):
        self.objects: dict[str, list[dict]] = {}
        self.next_id = 1000
        self.custom_fields: list[dict] = []
        self.writes: list[tuple] = []

    # --- reads
    def _matches(self, item, key, value):
        if key in ("limit", "offset", "brief", "has_primary_ip"):
            return True
        if key == "id" and isinstance(value, (list, tuple)):
            return item.get("id") in value
        if key.endswith("_id"):
            field = key[:-3]
            if field == "interface":
                return str(item.get("assigned_object_id")) == str(value)
            if value == "null":            # NetBox's spelling of "is not set"
                return _id(item.get(field)) is None
            return str(_id(item.get(field))) == str(value)
        if key == "name__ie":
            return str(item.get("name") or "").lower() == str(value).lower()
        current = item.get(key)
        if key == "address":
            return str(current or "").split("/")[0] == str(value).split("/")[0]
        if key == "serial":
            return str(current or "").lower() == str(value).lower()
        return str(_id(current)) == str(value)

    def all(self, path, params=None):
        params = params or {}
        return [dict(i) for i in self.objects.get(path, [])
                if all(self._matches(i, k, v) for k, v in params.items())]

    def first(self, path, params=None):
        found = self.all(path, params)
        return found[0] if found else None

    def get(self, path, params=None):
        parts = path.strip("/").split("/")
        if parts[-1].isdigit():
            collection = "/" + "/".join(parts[:-1]) + "/"
            for item in self.objects.get(collection, []):
                if item["id"] == int(parts[-1]):
                    return dict(item)
            raise NetBoxError("GET %s -> 404" % path)
        results = self.all(path, params)
        return {"count": len(results), "results": results, "next": None}

    # --- writes
    def _store(self, item, payload):
        for key, value in payload.items():
            if key in FK_FIELDS and isinstance(value, int):
                item[key] = {"id": value}
            elif key == "custom_fields":
                item.setdefault("custom_fields", {}).update(value)
            elif key == "vdcs":
                item[key] = [{"id": v} if isinstance(v, int) else v for v in value]
            else:
                item[key] = value

    def add(self, path, **fields):
        item = {"id": self.next_id}
        self.next_id += 1
        self._store(item, fields)
        self.objects.setdefault(path, []).append(item)
        return item

    def create(self, path, payload, label=""):
        item = self.add(path, **payload)
        self.writes.append(("create", path, dict(payload)))
        return dict(item)

    def update(self, path, object_id, payload, label=""):
        for item in self.objects.get(path, []):
            if item["id"] == object_id:
                self._store(item, payload)
                self.writes.append(("update", path, object_id, dict(payload)))
                return dict(item)
        raise NetBoxError("PATCH %s%s/ -> 404" % (path, object_id))

    def ensure(self, path, lookup, payload, label=""):
        return self.first(path, lookup) or self.create(path, payload, label)

    def ensure_fields(self, path, existing, desired, label=""):
        changes = {}
        for key, value in desired.items():
            if value in (None, ""):
                continue
            current = existing.get(key)
            if key == "custom_fields":
                have = {k: _id(v) for k, v in (current or {}).items()}
                if any(have.get(k) != _id(v) for k, v in value.items()):
                    changes[key] = value
            elif _id(current) != _id(value):
                changes[key] = value
        if not changes:
            return existing
        return self.update(path, existing["id"], changes, label)

    def ensure_custom_field(self, name, object_types, field_type="text", label="",
                            description="", **extra):
        self.custom_fields.append(dict(name=name, type=field_type, **extra))
        return {"id": 1, "name": name}

    def plugin_installed(self, name):
        return False

    def endpoint_available(self, path):
        return path.startswith("/plugins/discovery/")

    def post_raw(self, path, payload, label=""):
        return {}

    # --- convenience
    def devices(self):
        return self.objects.get("/dcim/devices/", [])

    def device(self, name):
        return next(d for d in self.devices() if d["name"] == name)

    def vdcs(self):
        return self.objects.get(VDC_ENDPOINT, [])


def syncer(netbox):
    return Syncer(netbox, SyncOptions(sync_cables=False, sync_fhrp_groups=False))


def chassis_in(netbox, name="n7k-1", address="10.0.0.11", serial=CHASSIS_SERIAL):
    ip = netbox.add("/ipam/ip-addresses/", address=f"{address}/24")
    device = netbox.add("/dcim/devices/", name=name, serial=serial, site=1,
                        primary_ip4={"id": ip["id"], "address": f"{address}/24"})
    mgmt = netbox.add("/dcim/interfaces/", device=device["id"], name="mgmt0", type="1000base-t",
                      enabled=True, vdcs=[])
    ip.update({"assigned_object_type": "dcim.interface", "assigned_object_id": mgmt["id"]})
    return device


class TestSyncingAVdc:
    def test_it_becomes_a_context_on_the_chassis(self, tmp_path):
        netbox = FakeNetBox()
        chassis = chassis_in(netbox)
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.12")

        # No second device for the same chassis.
        assert [d["name"] for d in netbox.devices()] == ["n7k-1"]
        vdc, = netbox.vdcs()
        assert vdc["name"] == "dmz" and vdc["identifier"] == 2
        assert _id(vdc["device"]) == chassis["id"]
        assert vdc["description"] == "Hostname n7k-1-dmz"

    def test_its_ports_are_the_chassis_ports_allocated_to_it(self, tmp_path):
        netbox = FakeNetBox()
        chassis = chassis_in(netbox)
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.12")
        vdc, = netbox.vdcs()
        by_name = {i["name"]: i for i in netbox.objects["/dcim/interfaces/"]}
        assert _id(by_name["Ethernet3/1"]["device"]) == chassis["id"]
        assert [_id(v) for v in by_name["Ethernet3/1"]["vdcs"]] == [vdc["id"]]
        # mgmt0 already existed on the chassis and is shared with the VDC.
        assert [_id(v) for v in by_name["mgmt0"]["vdcs"]] == [vdc["id"]]

    def test_its_address_is_the_contexts_primary_not_the_chassiss(self, tmp_path):
        netbox = FakeNetBox()
        chassis = chassis_in(netbox)
        chassis_primary = _id(chassis["primary_ip4"])
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.12")
        vdc, = netbox.vdcs()
        ip = next(i for i in netbox.objects["/ipam/ip-addresses/"]
                  if i["address"].startswith("10.0.0.12/"))
        assert _id(vdc["primary_ip4"]) == ip["id"]
        assert _id(netbox.device("n7k-1")["primary_ip4"]) == chassis_primary

    def test_a_rescan_changes_nothing(self, tmp_path):
        netbox = FakeNetBox()
        chassis_in(netbox)
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.12")
        before = len(netbox.writes)
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.12")
        assert len(netbox.vdcs()) == 1
        assert netbox.writes[before:] == []

    def test_without_the_chassis_nothing_is_written(self, tmp_path, caplog):
        """A VDC is not created as a device of its own: the chassis's own
        scan is what creates the chassis."""
        netbox = FakeNetBox()
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        with caplog.at_level(logging.WARNING):
            syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.12")
        assert netbox.devices() == [] and netbox.vdcs() == []
        assert "Scan the chassis (its default VDC) first" in caplog.text

    def test_a_record_made_from_this_vdc_earlier_stays_the_chassis(self, tmp_path, caplog):
        """Before VDCs were understood the first VDC scanned created the
        device. That record must not become a context of itself."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="n7k-1-dmz", address="10.0.0.12")
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        with caplog.at_level(logging.WARNING):
            syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.12")
        assert netbox.vdcs() == []
        assert [d["name"] for d in netbox.devices()] == ["n7k-1-dmz"]
        assert "recorded from a VDC rather than from the default VDC" in caplog.text

    def test_the_default_vdc_scan_becomes_its_own_record_beside_that(self, tmp_path, caplog):
        """The chassis answering under its own name and address while a
        VDC-made record holds the serial agrees with it on neither, so it is
        a second record with the same serial -- never an overwrite."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="n7k-1-dmz", address="10.0.0.12")
        result = scan(nexus_walk(tmp_path, "n7k-1", [(1, "n7k-1"), (2, "dmz")]), "10.0.0.11")
        with caplog.at_level(logging.WARNING):
            syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.11")
        assert [d["name"] for d in netbox.devices()] == ["n7k-1-dmz", "n7k-1"]
        assert netbox.device("n7k-1-dmz")["primary_ip4"]  # untouched
        assert "'n7k-1' is treated as a separate device with the same serial" in caplog.text


class TestSyncingAVcmpGuest:
    def guest(self, tmp_path):
        return scan(f5_walk(tmp_path, "ltm-guest-01", "Z101", "BIG-IP vCMP Guest"), "10.0.0.20")

    def test_it_is_a_device_of_its_own_linked_to_the_host(self, tmp_path):
        netbox = FakeNetBox()
        host = chassis_in(netbox, name="vcmp-host-01", address="10.0.0.21", serial=HOST_SERIAL)
        syncer(netbox).sync(self.guest(tmp_path), site_id=1, scanned_address="10.0.0.20")
        guest = netbox.device("ltm-guest-01")
        assert guest.get("serial", "") == ""
        assert _id(guest["custom_fields"][VCMP_HOST_FIELD]) == host["id"]
        # The host itself is untouched.
        assert netbox.device("vcmp-host-01")["serial"] == HOST_SERIAL
        field, = [f for f in netbox.custom_fields if f["name"] == VCMP_HOST_FIELD]
        assert field["type"] == "object" and field["related_object_type"] == "dcim.device"

    def test_the_host_arriving_later_is_linked_on_the_next_sweep(self, tmp_path, caplog):
        netbox = FakeNetBox()
        with caplog.at_level(logging.INFO):
            syncer(netbox).sync(self.guest(tmp_path), site_id=1, scanned_address="10.0.0.20")
        assert "is not in NetBox yet" in caplog.text
        assert VCMP_HOST_FIELD not in (netbox.device("ltm-guest-01").get("custom_fields") or {})
        host = chassis_in(netbox, name="vcmp-host-01", address="10.0.0.21", serial=HOST_SERIAL)
        syncer(netbox).sync(self.guest(tmp_path), site_id=1, scanned_address="10.0.0.20")
        assert _id(netbox.device("ltm-guest-01")["custom_fields"][VCMP_HOST_FIELD]) == host["id"]

    def test_a_guest_recorded_with_the_hosts_serial_gives_it_up(self, tmp_path, caplog):
        """Before guests were understood, the first guest scanned was created
        with the chassis serial; the host's own scan then collided with it."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="ltm-guest-01", address="10.0.0.20", serial=HOST_SERIAL)
        host = chassis_in(netbox, name="vcmp-host-01", address="10.0.0.21", serial=HOST_SERIAL)
        with caplog.at_level(logging.WARNING):
            syncer(netbox).sync(self.guest(tmp_path), site_id=1, scanned_address="10.0.0.20")
        guest = netbox.device("ltm-guest-01")
        assert guest["serial"] == ""
        assert _id(guest["custom_fields"][VCMP_HOST_FIELD]) == host["id"]
        assert "moving it off the guest" in caplog.text


class TestWhichRecordAScanIs:
    """A scan never lands on a record that might be a different box.

    Duplicate serials are allowed and never refused or reported; what is
    refused is the guess. The overwrite the user saw came from a serial alone
    claiming the first record carrying it, and from a name alone claiming a
    record at any site.
    """

    def record(self, name, serial=CHASSIS_SERIAL):
        return DeviceRecord(name=name, serial=serial, model="N7K-C7010",
                            manufacturer="Cisco", platform="Cisco NX-OS")

    def find(self, netbox, record, address):
        return syncer(netbox)._find_device(record, 1, address)

    def test_a_serial_on_two_records_goes_to_the_one_with_this_name(self):
        netbox = FakeNetBox()
        chassis_in(netbox, name="alpha", address="10.0.0.1")
        beta = chassis_in(netbox, name="beta", address="10.0.0.2")
        assert self.find(netbox, self.record("beta"), "10.0.0.99")["id"] == beta["id"]

    def test_or_the_one_at_this_address(self):
        netbox = FakeNetBox()
        chassis_in(netbox, name="alpha", address="10.0.0.1")
        beta = chassis_in(netbox, name="beta", address="10.0.0.2")
        assert self.find(netbox, self.record("renamed"), "10.0.0.2")["id"] == beta["id"]

    def test_agreeing_with_neither_makes_a_separate_record_not_an_overwrite(self, caplog):
        netbox = FakeNetBox()
        chassis_in(netbox, name="alpha", address="10.0.0.1")
        chassis_in(netbox, name="beta", address="10.0.0.2")
        assert self.find(netbox, self.record("gamma"), "10.0.0.9") is None
        result = ScanResult(host="10.0.0.9", devices=[self.record("gamma")])
        with caplog.at_level(logging.WARNING):
            syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.9")
        assert [d["name"] for d in netbox.devices()] == ["alpha", "beta", "gamma"]
        assert [d["serial"] for d in netbox.devices()] == [CHASSIS_SERIAL] * 3
        assert "separate device with the same serial" in caplog.text
        # Never raised as an issue: duplicates are allowed, not reported.
        assert "/plugins/discovery/issues/" not in netbox.objects

    def test_a_lone_record_with_the_serial_is_not_claimed_on_the_serial_alone(self):
        """A rename and a re-address at once looks exactly like a different
        box with a duplicate serial, and the safe reading is the second."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="alpha", address="10.0.0.1")
        assert self.find(netbox, self.record("renamed-and-moved"), "10.0.0.99") is None

    def test_a_record_made_without_a_serial_gets_it_when_the_box_reports_one(self):
        """Entered by hand or imported from a sheet, then scanned."""
        netbox = FakeNetBox()
        sheet = chassis_in(netbox, name="core-sw-01", address="10.0.0.1", serial="")
        found = self.find(netbox, self.record("core-sw-01"), "10.0.0.1")
        assert found["id"] == sheet["id"]
        result = ScanResult(host="10.0.0.1", devices=[self.record("core-sw-01")])
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.1")
        assert netbox.device("core-sw-01")["serial"] == CHASSIS_SERIAL
        assert len(netbox.devices()) == 1

    # --- no serial at all: printers, VMs, gear with an empty ENTITY-MIB

    def test_no_serial_is_matched_by_its_address(self):
        netbox = FakeNetBox()
        alpha = chassis_in(netbox, name="alpha", address="10.0.0.1", serial="")
        assert self.find(netbox, self.record("renamed", serial=""), "10.0.0.1")["id"] == alpha["id"]

    def test_no_serial_is_matched_by_name_at_the_scanned_site(self):
        netbox = FakeNetBox()
        alpha = chassis_in(netbox, name="core-sw-01", address="10.0.0.1", serial="")
        assert self.find(netbox, self.record("core-sw-01", serial=""), "10.0.0.7")["id"] == alpha["id"]

    def test_the_same_name_at_another_site_is_a_different_box(self):
        """Branch sites reuse hostnames. This is what made devices with no
        serial overwrite each other, and move sites while they were at it."""
        netbox = FakeNetBox()
        other_site = chassis_in(netbox, name="core-sw-01", address="10.0.0.1", serial="")
        netbox.objects["/dcim/devices/"][0]["site"] = {"id": 2}
        assert self.find(netbox, self.record("core-sw-01", serial=""), "10.0.0.7") is None
        result = ScanResult(host="10.0.0.7", devices=[self.record("core-sw-01", serial="")])
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.7")
        assert len(netbox.devices()) == 2
        assert _id(netbox.objects["/dcim/devices/"][0]["site"]) == 2  # not moved
        assert netbox.objects["/dcim/devices/"][0]["id"] == other_site["id"]


class TestEnteredByHand:
    """A device from a manually entered request keeps what was typed."""

    def manual_device(self, netbox):
        device = chassis_in(netbox, name="fw-typed", address="10.0.0.5", serial="TYPED-1")
        device["tags"] = [{"slug": MANUAL_TAG, "name": "Entered by hand"}]
        device["device_type"] = {"id": 77, "model": "FPR-2120"}
        return device

    def test_a_later_scan_does_not_change_its_identity(self):
        netbox = FakeNetBox()
        device = self.manual_device(netbox)
        record = DeviceRecord(name="fw-typed", serial="READ-2", model="FPR-2130",
                              manufacturer="Cisco", platform="Cisco FTD",
                              software_version="7.2.5")
        result = ScanResult(host="10.0.0.5", devices=[record])
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.5")
        after = netbox.device("fw-typed")
        assert after["serial"] == "TYPED-1"
        assert _id(after["device_type"]) == 77
        assert len(netbox.devices()) == 1              # nothing retired or created
        assert after.get("custom_fields", {}).get("software_version") == "7.2.5"  # observations still land

    def test_blanks_are_still_filled_in(self):
        netbox = FakeNetBox()
        device = self.manual_device(netbox)
        device["serial"] = ""
        record = DeviceRecord(name="fw-typed", serial="READ-2", model="FPR-2130",
                              manufacturer="Cisco")
        syncer(netbox).sync(ScanResult(host="10.0.0.5", devices=[record]),
                            site_id=1, scanned_address="10.0.0.5")
        assert netbox.device("fw-typed")["serial"] == "READ-2"


# --- the onboarding round trip ------------------------------------------------


class TestOnboarding:
    def test_the_payload_says_what_the_scan_is(self, tmp_path):
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        payload = scan_payload(result)
        assert payload["devices"][0]["context"] == {
            "kind": "vdc", "chassis_serial": CHASSIS_SERIAL, "name": "dmz", "identifier": 2,
            "detail": "VDC 2 'dmz' of the chassis with serial JAF1234ABCD",
        }
        rebuilt = scan_result_from_payload(payload, host="10.0.0.12")
        assert rebuilt.primary.context == result.primary.context

    def test_a_box_of_its_own_says_none(self, tmp_path):
        result = scan(nexus_walk(tmp_path, "n9k-1", []), "10.0.0.13")
        assert scan_payload(result)["devices"][0]["context"] is None
        assert scan_result_from_payload(scan_payload(result)).primary.context is None

    def test_the_device_reported_for_a_vdc_is_its_chassis(self, tmp_path):
        netbox = FakeNetBox()
        chassis = chassis_in(netbox)
        result = scan(nexus_walk(tmp_path, "n7k-1-dmz", [(2, "dmz")]), "10.0.0.12")
        assert _find_created_device(netbox, result, site_id=1)["id"] == chassis["id"]

    def test_a_duplicate_serial_request_reports_its_own_device_not_the_original(self):
        netbox = FakeNetBox()
        chassis_in(netbox, name="alpha", address="10.0.0.1")
        mine = netbox.add("/dcim/devices/", name="gamma", serial=CHASSIS_SERIAL, site=1)
        result = ScanResult(host="10.0.0.9", devices=[DeviceRecord(name="gamma", serial=CHASSIS_SERIAL)])
        assert _find_created_device(netbox, result, site_id=1)["id"] == mine["id"]

    def test_a_guest_is_found_by_name_since_it_has_no_serial(self, tmp_path):
        netbox = FakeNetBox()
        guest = netbox.add("/dcim/devices/", name="ltm-guest-01", site=1)
        result = scan(f5_walk(tmp_path, "ltm-guest-01", "Z101", "BIG-IP vCMP Guest"), "10.0.0.20")
        assert _find_created_device(netbox, result, site_id=1)["id"] == guest["id"]
