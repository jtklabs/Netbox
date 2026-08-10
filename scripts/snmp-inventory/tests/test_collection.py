"""Collection and modelling, driven from recorded walks.

These assert the facts the scanner is supposed to get right — models reported
verbatim, serials per stack member, interfaces landing on the correct member,
software versions from wherever each vendor publishes them. They run against
fixtures with no network, so a failure points at the code rather than at the
lab.
"""

from __future__ import annotations

from conftest import collect_fixture

from snmpinv import mibs
from snmpinv.model import build_scan_result


def scan(name: str):
    return build_scan_result(collect_fixture(name))


class TestCiscoStack:
    """The case the old pipeline handled worst."""

    def setup_method(self):
        self.result = scan("cisco-c9300-stack")

    def test_is_a_stack_of_three(self):
        assert self.result.is_stack
        assert len(self.result.devices) == 3
        assert self.result.virtual_chassis_name == "bld-a-core-01"

    def test_each_member_keeps_its_own_serial_and_model(self):
        by_position = {d.vc_position: d for d in self.result.devices}
        assert by_position[1].serial == "FOC2530L0AB"
        assert by_position[2].serial == "FOC2530L0CD"
        assert by_position[3].serial == "FOC2531L0EF"
        # Member 3 is a different model from members 1 and 2 — a stack is not
        # required to be homogeneous, and collapsing it to one model loses that.
        assert by_position[1].model == "C9300-48P"
        assert by_position[3].model == "C9300-24P"

    def test_master_comes_from_stackwise_role(self):
        masters = [d for d in self.result.devices if d.vc_is_master]
        assert len(masters) == 1
        assert masters[0].vc_position == 1
        # The master keeps the stack's own name so the address an operator
        # already uses still points at it.
        assert masters[0].name == "bld-a-core-01"
        assert {d.name for d in self.result.devices} == {
            "bld-a-core-01", "bld-a-core-01-2", "bld-a-core-01-3"
        }

    def test_interfaces_land_on_the_member_that_owns_the_port(self):
        by_position = {d.vc_position: d for d in self.result.devices}
        for position in (1, 2, 3):
            names = [i.name for i in by_position[position].interfaces]
            # Only three-part names encode a stack member. GigabitEthernet0/0
            # is the out-of-band management port and belongs to the master
            # regardless of its leading number.
            stack_ports = [n for n in names if n.count("/") == 2]
            assert stack_ports, f"member {position} has no physical interfaces"
            for name in stack_ports:
                # GigabitEthernet2/0/1 belongs to member 2, and nowhere else.
                assert name.split("Ethernet")[1].split("/")[0] == str(position), name

    def test_two_part_interface_names_are_not_treated_as_member_numbers(self):
        """GigabitEthernet0/0 must not be filed under a member 0 that does not
        exist — two-part names are slot/port, not member/slot/port."""
        master = self.result.primary
        assert "GigabitEthernet0/0" in [i.name for i in master.interfaces]

    def test_logical_interfaces_go_to_the_master(self):
        master = self.result.primary
        names = [i.name for i in master.interfaces]
        assert "Port-channel1" in names
        assert "Vlan10" in names

    def test_each_member_gets_its_own_uplink_module(self):
        for device in self.result.devices:
            models = [m.model for m in device.modules]
            assert "C9300-NM-8X" in models, f"{device.name} has no uplink module"
        serials = {m.serial for d in self.result.devices for m in d.modules}
        assert len(serials) == 3, "each member's module must keep its own serial"

    def test_power_supplies_are_not_modules(self):
        """entPhysicalClass 6 is a power supply, not a module — creating module
        bays for PSUs clutters every device in NetBox."""
        for device in self.result.devices:
            assert not any("PWR-" in m.model for m in device.modules)

    def test_software_version_from_sysdescr(self):
        assert self.result.primary.software_version == "17.03.04a"

    def test_platform_is_iosxe_not_plain_ios(self):
        assert self.result.primary.platform == "Cisco IOS-XE"

    def test_management_ip_is_attached_to_its_interface(self):
        master = self.result.primary
        vlan = [i for i in master.interfaces if i.name == "Vlan10"][0]
        assert vlan.ip_addresses == ["10.10.1.5/24"]

    def test_interface_types_map_by_speed(self):
        master = self.result.primary
        by_name = {i.name: i for i in master.interfaces}
        assert by_name["GigabitEthernet1/0/1"].type_slug == "1000base-t"
        assert by_name["TenGigabitEthernet1/1/1"].type_slug == "10gbase-x-sfpp"
        assert by_name["Port-channel1"].type_slug == "lag"
        assert by_name["Vlan10"].type_slug == "virtual"
        # The out-of-band port is copper even though it is not 10G.
        assert by_name["GigabitEthernet0/0"].type_slug == "1000base-t"

    def test_macs_are_normalised(self):
        master = self.result.primary
        by_name = {i.name: i for i in master.interfaces}
        assert by_name["GigabitEthernet1/0/1"].mac_address == "AC:F2:C5:01:01:01"


class TestSingleSwitchIsNotAStack:
    """A 2960X answers the stackwise MIB with one member. One member is a
    standalone switch, not a virtual chassis of one."""

    def setup_method(self):
        self.result = scan("cisco-2960x")

    def test_not_a_stack(self):
        assert not self.result.is_stack
        assert len(self.result.devices) == 1
        assert self.result.virtual_chassis_name == ""

    def test_model_and_serial(self):
        device = self.result.primary
        assert device.model == "WS-C2960X-48FPD-L"
        assert device.serial == "FOC1934X0AB"
        assert device.manufacturer == "Cisco"

    def test_version(self):
        assert self.result.primary.software_version == "15.2(7)E3"


class TestArista:
    """The model must survive exactly as reported — this is the bug being fixed."""

    def setup_method(self):
        self.result = scan("arista-7050sx")

    def test_model_is_verbatim(self):
        device = self.result.primary
        assert device.model == "DCS-7050SX-72Q"
        # Not the mangled form the sysObjectID lookup tables produce.
        assert device.model != "aristaDCS7050SX272Q"

    def test_manufacturer_is_separate_from_model(self):
        assert self.result.primary.manufacturer == "Arista Networks"
        assert "Arista" not in self.result.primary.model

    def test_version_and_serial(self):
        assert self.result.primary.software_version == "4.29.2F"
        assert self.result.primary.serial == "JPE17240001"


class TestArubaController:
    def setup_method(self):
        self.result = scan("aruba-7010-wlc")

    def test_controller_identity(self):
        device = self.result.primary
        assert device.model == "Aruba7010"
        assert device.manufacturer == "Aruba Networks"
        assert device.software_version == "8.10.0.4"

    def test_access_points_are_discovered(self):
        assert len(self.result.access_points) == 3
        names = {ap.name for ap in self.result.access_points}
        assert names == {"dal-ap-101", "dal-ap-102", "dal-ap-103"}

    def test_access_points_keep_model_and_serial(self):
        by_name = {ap.name: ap for ap in self.result.access_points}
        assert by_name["dal-ap-101"].model == "AP-515"
        assert by_name["dal-ap-103"].model == "AP-535"
        assert by_name["dal-ap-101"].serial == "CNJPJ0A001"
        assert all(ap.is_access_point for ap in self.result.access_points)


class TestApplianceVendors:
    """Firewalls and load balancers leave ENTITY-MIB empty; the model, serial
    and version have to come from each vendor's own scalars."""

    def test_palo_alto(self):
        device = scan("palo-pa3220").primary
        assert device.model == "PA-3220"
        assert device.serial == "013101011234"
        assert device.software_version == "11.1.4-h7"
        assert device.platform == "PAN-OS"

    def test_fortigate(self):
        device = scan("fortigate-600e").primary
        assert device.serial == "FG600ETK21901234"
        assert device.software_version.startswith("v7.2.8")
        assert device.platform == "FortiOS"

    def test_f5(self):
        device = scan("f5-bigip").primary
        assert device.model == "BIG-IP i5800"
        assert device.serial == "f5-chs-01234567"
        assert device.software_version == "17.1.1.3"

    def test_checkpoint(self):
        device = scan("checkpoint-gaia").primary
        assert device.model == "Check Point 6200"
        assert device.serial == "1811B00234"
        assert device.software_version == "R81.20"

    def test_infoblox(self):
        device = scan("infoblox-nios").primary
        assert device.model == "IB-1420"
        assert device.serial == "422900123456789"
        assert device.software_version == "9.0.4-50212"

    def test_juniper(self):
        device = scan("juniper-ex4300").primary
        assert device.model == "EX4300-48T"
        assert device.serial == "PE3714AF0123"
        assert device.software_version == "21.4R3-S4.9"
        assert device.platform == "Junos"

    def test_opengear(self):
        device = scan("opengear-cm7148").primary
        assert device.software_version == "4.13.0"
        assert device.manufacturer == "Opengear"

    def test_clearpass(self):
        device = scan("aruba-clearpass").primary
        assert device.software_version.startswith("6.11.5")
        assert device.platform == "Aruba ClearPass"


class TestNeverGuessesAModel:
    """A device that reports no model must not get an invented one.

    The sync layer then refuses to create it and logs why, which is a visible
    gap an operator can act on — as opposed to a plausible-looking wrong model
    that somebody has to notice and then un-learn.
    """

    def test_missing_model_stays_empty(self):
        from snmpinv.collect import DeviceFacts

        facts = DeviceFacts(host="192.0.2.9", sys_name="mystery-box")
        # A vendor we have no profile for, with an empty ENTITY-MIB.
        facts.sys_object_id = "1.3.6.1.4.1.99999.1.1"
        facts.sys_descr = "Some Appliance 9000, firmware 3.2"
        result = build_scan_result(facts)
        assert result.primary.model == ""
        assert result.primary.manufacturer == ""

    def test_sysobjectid_subarcs_never_become_a_model(self):
        """The enterprise arc names the vendor; nothing below it is consulted.

        Two different Cisco platforms have different sysObjectIDs and must
        still both resolve to plain "Cisco" with no model inferred.
        """
        from snmpinv.collect import DeviceFacts

        for oid in ("1.3.6.1.4.1.9.1.2494", "1.3.6.1.4.1.9.1.1745"):
            facts = DeviceFacts(host="192.0.2.9", sys_name="sw", sys_object_id=oid)
            result = build_scan_result(facts)
            assert result.primary.manufacturer == "Cisco"
            assert result.primary.model == ""


class TestInterfaceTypeMapping:
    def test_speeds(self):
        cases = [
            (mibs.IF_TYPE_ETHERNET, 100, "100base-tx"),
            (mibs.IF_TYPE_ETHERNET, 1000, "1000base-t"),
            (mibs.IF_TYPE_ETHERNET, 2500, "2.5gbase-t"),
            (mibs.IF_TYPE_ETHERNET, 10000, "10gbase-x-sfpp"),
            (mibs.IF_TYPE_ETHERNET, 25000, "25gbase-x-sfp28"),
            (mibs.IF_TYPE_ETHERNET, 40000, "40gbase-x-qsfpp"),
            (mibs.IF_TYPE_ETHERNET, 100000, "100gbase-x-qsfp28"),
        ]
        for if_type, speed, expected in cases:
            assert mibs.netbox_interface_type(if_type, speed, "Ethernet1") == expected

    def test_virtual_and_lag(self):
        assert mibs.netbox_interface_type(mibs.IF_TYPE_IEEE8023AD_LAG, None, "Po1") == "lag"
        assert mibs.netbox_interface_type(mibs.IF_TYPE_L2_VLAN, None, "Vlan1") == "virtual"
        assert mibs.netbox_interface_type(mibs.IF_TYPE_SOFTWARE_LOOPBACK, None, "Lo0") == "virtual"
        assert mibs.netbox_interface_type(mibs.IF_TYPE_TUNNEL, None, "Tu0") == "virtual"
        assert mibs.netbox_interface_type(mibs.IF_TYPE_BRIDGE, None, "br0") == "bridge"

    def test_unmappable_speeds_become_other_not_a_wrong_guess(self):
        # NetBox has no 10 Mbps Ethernet type at all.
        assert mibs.netbox_interface_type(mibs.IF_TYPE_ETHERNET, 10, "Eth0") == "other"
        # And an unheard-of speed must not be rounded to a neighbour.
        assert mibs.netbox_interface_type(mibs.IF_TYPE_ETHERNET, 7777, "Eth0") == "other"

    def test_unknown_iftype_is_kept_as_other(self):
        assert mibs.netbox_interface_type(999, 1000, "Weird0") == "other"


class TestSoftwareVersionIsRaw:
    """Versions are passed on exactly as reported.

    The Lifecycle plugin owns version comparison, so anything normalised here
    would be a parsing decision baked in at the wrong layer — and one that
    could not be corrected later without rescanning the fleet.
    """

    def test_fortinet_build_metadata_is_not_stripped(self):
        version = scan("fortigate-600e").primary.software_version
        assert version == "v7.2.8,build1639,240110 (GA)"

    def test_cisco_version_is_not_zero_padded(self):
        assert scan("cisco-c9300-stack").primary.software_version == "17.03.04a"

    def test_junos_service_release_suffix_kept(self):
        assert scan("juniper-ex4300").primary.software_version == "21.4R3-S4.9"

    def test_collection_is_timestamped(self):
        """collected_at stamps when the device was walked, so a stale reading
        is legible as stale rather than as fresh as the run that reported it."""
        from datetime import datetime, timezone

        facts = collect_fixture("arista-7050sx")
        assert facts.collected_at is not None
        assert facts.collected_at.tzinfo is not None, "must be timezone-aware"
        age = (datetime.now(timezone.utc) - facts.collected_at).total_seconds()
        assert 0 <= age < 60


class TestIpDecoding:
    def test_ipv4_prefix_length_comes_from_the_row_pointer(self):
        facts = collect_fixture("cisco-c9300-stack")
        assert len(facts.ips) == 1
        entry = facts.ips[0]
        assert entry.address == "10.10.1.5"
        # /24 is encoded only in the last sub-identifier of ipAddressPrefix.
        assert entry.prefix_length == 24
        assert entry.cidr() == "10.10.1.5/24"


# --- GETBULK fallback -------------------------------------------------------
#
# A device that answers GETs but never answers a GETBULK is a real and
# confusing failure: the reply to one GETBULK carries max_repetitions varbinds
# at once, and once it passes the path MTU it is fragmented and something in
# between drops it. Nothing comes back, so it looks exactly like an unreachable
# host — and because it depends on how much data the device has rather than
# what it is, two identical models can behave differently.

import pytest

from snmpinv.snmp import (
    Credential,
    CredentialSession,
    SnmpAuthError,
    SnmpTimeoutError,
    VarBind,
    parse_varbinds,
)

_ONE_BIND = ".1.3.6.1.2.1.1.5.0 = STRING: sw1\n"


def _session(behaviour):
    """A session whose net-snmp calls are scripted. Records the tools used."""
    session = CredentialSession(
        Credential(name="t", security_name="u", auth_passphrase="a", priv_passphrase="b"),
        timeout=1, retries=0,
    )
    calls = []

    def fake_run(tool, host, oid, numeric_timeticks=True):
        calls.append(tool)
        return behaviour(tool)

    session._run = fake_run
    return session, calls


def _bulk_times_out(tool):
    if tool == "snmpbulkwalk":
        raise SnmpTimeoutError("host: no response")
    return _ONE_BIND


class TestGetbulkFallback:
    def test_a_host_that_has_answered_falls_back_to_getnext(self):
        session, calls = _session(_bulk_times_out)
        session.answered = True          # a GET already came back

        binds = session.walk("192.0.2.1", "1.3.6.1.2.1.1")

        assert [b.value for b in binds] == ["sw1"]
        assert calls == ["snmpbulkwalk", "snmpwalk"]

    def test_a_silent_host_is_not_probed_twice(self):
        """The reason the fallback is conditional: a dead host must not cost
        two full timeouts on every subtree."""
        session, calls = _session(_bulk_times_out)
        session.answered = False

        with pytest.raises(SnmpTimeoutError):
            session.walk("192.0.2.1", "1.3.6.1.2.1.1")
        assert calls == ["snmpbulkwalk"]

    def test_the_fallback_latches_for_the_rest_of_the_session(self):
        """Otherwise every remaining subtree pays the GETBULK timeout again —
        on a device with a dozen tables that is minutes of pure waiting."""
        session, calls = _session(_bulk_times_out)
        session.answered = True

        session.walk("192.0.2.1", "1.3.6.1.2.1.1")
        session.walk("192.0.2.1", "1.3.6.1.2.1.2")
        session.walk("192.0.2.1", "1.3.6.1.2.1.4")

        assert session.use_bulk is False
        assert calls == ["snmpbulkwalk", "snmpwalk", "snmpwalk", "snmpwalk"]

    def test_an_auth_failure_never_falls_back(self):
        """Retrying with GETNEXT would fail identically and waste a round trip."""
        def auth_fails(tool):
            raise SnmpAuthError("host: Authentication failure")

        session, calls = _session(auth_fails)
        session.answered = True

        with pytest.raises(SnmpAuthError):
            session.walk("192.0.2.1", "1.3.6.1.2.1.1")
        assert calls == ["snmpbulkwalk"]

    def test_a_successful_get_marks_the_host_as_answering(self):
        session, _ = _session(lambda tool: _ONE_BIND)
        assert session.answered is False
        session._run = lambda *a, **k: _ONE_BIND
        # get() builds its own argv, so drive it through the public path with a
        # stubbed subprocess instead.
        import subprocess as sp

        class Done:
            returncode, stdout, stderr = 0, _ONE_BIND, ""

        original, sp.run = sp.run, lambda *a, **k: Done()
        try:
            session._dir = "/tmp"
            session.get("192.0.2.1", ["1.3.6.1.2.1.1.2.0"])
        finally:
            sp.run = original
        assert session.answered is True


class TestGetBeforeWalk:
    """The collector must establish the host answers *before* it walks."""

    def test_a_get_precedes_the_first_walk(self, monkeypatch):
        order = []

        class FakeSession:
            def __init__(self, *a, **k):
                self.answered = False
                self.use_bulk = True

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def probe(self, host):
                order.append("get")
                self.answered = True
                return True

            def walk(self, host, oid):
                order.append("walk")
                return parse_varbinds(
                    ".1.3.6.1.2.1.1.5.0 = STRING: sw1\n"
                    ".1.3.6.1.2.1.1.2.0 = OID: .1.3.6.1.4.1.9.1.2494\n"
                )

            def get(self, host, oids):
                return {}

        from snmpinv import collect as collect_module
        from snmpinv.snmp import parse_varbinds

        monkeypatch.setattr(collect_module, "CredentialSession", FakeSession)
        collector = collect_module.Collector(
            [Credential(name="t", security_name="u", auth_passphrase="a")]
        )
        facts = collector.collect("192.0.2.1")

        assert order[0] == "get", "a walk was attempted before any GET"
        assert facts.sys_name == "sw1"

    def test_a_silent_host_never_reaches_a_walk(self, monkeypatch):
        """So it costs one small timeout, not one per credential set per table."""
        attempted = []

        class SilentSession:
            def __init__(self, *a, **k):
                self.answered = False

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def probe(self, host):
                attempted.append("get")
                raise SnmpTimeoutError("192.0.2.1: no response")

            def walk(self, host, oid):
                attempted.append("walk")
                return []

        from snmpinv import collect as collect_module

        monkeypatch.setattr(collect_module, "CredentialSession", SilentSession)
        collector = collect_module.Collector([
            Credential(name="one", security_name="u", auth_passphrase="a"),
            Credential(name="two", security_name="v", auth_passphrase="b"),
        ])
        with pytest.raises(SnmpTimeoutError):
            collector.collect("192.0.2.1")
        # One GET total: not one per credential set.
        assert attempted == ["get"]
