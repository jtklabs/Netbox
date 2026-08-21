"""End-to-end over real SNMPv3, against emulated devices.

Everything here goes over a socket to a real snmpd doing real USM crypto. That
is what makes these worth the extra seconds they cost: they exercise the parts
the replay tests cannot reach — the snmp.conf mechanism, whether net-snmp
accepts the arguments we build, credential fallback between sets, GETBULK, and
the exact bytes net-snmp prints for a MAC.

Skipped automatically when net-snmp is not installed.
"""

from __future__ import annotations

import pytest
from conftest import LAB_CREDENTIAL, fixture_path, needs_snmpd
from emulator import EmulatedDevice

from snmpinv.collect import Collector
from snmpinv.model import build_scan_result
from snmpinv.snmp import Credential, CredentialSession, SnmpAuthError, SnmpTimeoutError

pytestmark = needs_snmpd

# Ports are picked per test class to keep concurrent runs from colliding.
BASE_PORT = 11610


WRONG_PASSPHRASE = Credential(
    name="wrong-passphrase",
    security_name="netops",
    auth_protocol="SHA-256",
    auth_passphrase="not-the-right-one",
    priv_protocol="AES",
    priv_passphrase="also-wrong",
)
WRONG_USER = Credential(
    name="wrong-user",
    security_name="nobody",
    auth_protocol="SHA-256",
    auth_passphrase="labauthpass123",
    priv_protocol="AES",
    priv_passphrase="labprivpass123",
)


@pytest.fixture(scope="module")
def cisco_stack():
    with EmulatedDevice(fixture_path("cisco-c9300-stack"), port=BASE_PORT) as device:
        yield device


@pytest.fixture(scope="module")
def arista():
    with EmulatedDevice(fixture_path("arista-7050sx"), port=BASE_PORT + 1) as device:
        yield device


class TestRealSnmpV3:
    def test_walk_over_the_wire(self, cisco_stack):
        with CredentialSession(LAB_CREDENTIAL) as session:
            binds = session.walk(cisco_stack.listen + ":" + str(cisco_stack.port),
                                 "1.3.6.1.2.1.1")
        by_oid = {b.oid: b for b in binds}
        assert "Version 17.03.04a" in by_oid["1.3.6.1.2.1.1.1.0"].value
        assert by_oid["1.3.6.1.2.1.1.5.0"].value == "bld-a-core-01"

    def test_hex_string_mac_survives_the_wire(self, cisco_stack):
        """snmpd prints a binary octet string as Hex-STRING. This is the only
        test that proves the scanner parses what a real agent actually emits."""
        with CredentialSession(LAB_CREDENTIAL) as session:
            binds = session.walk(cisco_stack.address, "1.3.6.1.2.1.2.2.1.6")
        values = {b.value for b in binds}
        assert "AC:F2:C5:01:01:01" in values

    def test_long_sysdescr_survives_the_wire(self, cisco_stack):
        """The whole sysDescr arrives, not just its first clause.

        Note what this does *not* cover: net-snmp's pass_persist protocol is
        line-based, so the emulator flattens the fixture's embedded newlines to
        spaces before serving. Real devices do send multi-line sysDescr, and
        that case is covered on recorded text instead, in
        test_parsing.py::test_multiline_value_is_joined.
        """
        with CredentialSession(LAB_CREDENTIAL) as session:
            binds = session.get(cisco_stack.address, ["1.3.6.1.2.1.1.1.0"])
        descr = binds["1.3.6.1.2.1.1.1.0"].value
        assert "Version 17.03.04a" in descr
        assert "Technical Support" in descr
        assert descr.endswith("by mcpre")

    def test_bulk_and_getnext_agree(self, cisco_stack):
        """GETBULK is the default because it is much faster on interface
        tables; it must not return anything different from GETNEXT."""
        with CredentialSession(LAB_CREDENTIAL, use_bulk=True) as session:
            bulk = session.walk(cisco_stack.address, "1.3.6.1.2.1.31.1.1.1.1")
        with CredentialSession(LAB_CREDENTIAL, use_bulk=False) as session:
            plain = session.walk(cisco_stack.address, "1.3.6.1.2.1.31.1.1.1.1")
        assert [(b.oid, b.value) for b in bulk] == [(b.oid, b.value) for b in plain]
        assert len(bulk) == 18


class TestCredentialHandling:
    def test_wrong_passphrase_is_an_auth_error(self, arista):
        with CredentialSession(WRONG_PASSPHRASE) as session:
            with pytest.raises(SnmpAuthError):
                session.walk(arista.address, "1.3.6.1.2.1.1")

    def test_unknown_user_is_an_auth_error(self, arista):
        with CredentialSession(WRONG_USER) as session:
            with pytest.raises(SnmpAuthError):
                session.walk(arista.address, "1.3.6.1.2.1.1")

    def test_collector_falls_through_to_the_working_set(self, arista):
        """The behaviour the multi-credential design exists for: two bad sets
        in front of the good one, and the scan still succeeds."""
        collector = Collector([WRONG_USER, WRONG_PASSPHRASE, LAB_CREDENTIAL], timeout=2)
        facts = collector.collect(arista.address)
        assert facts.credential_name == "lab"
        assert facts.sys_name == "dc1-spine-01"

    def test_all_credentials_rejected_raises_auth_error(self, arista):
        collector = Collector([WRONG_USER, WRONG_PASSPHRASE], timeout=2)
        with pytest.raises(SnmpAuthError):
            collector.collect(arista.address)

    def test_dead_host_times_out_without_trying_every_credential(self):
        """A silent host must not cost one full timeout per credential set."""
        import time

        collector = Collector([WRONG_USER, WRONG_PASSPHRASE, LAB_CREDENTIAL],
                              timeout=1, retries=0)
        started = time.time()
        with pytest.raises(SnmpTimeoutError):
            # Nothing is listening here.
            collector.collect("127.0.0.1:11699")
        elapsed = time.time() - started
        assert elapsed < 8, f"took {elapsed:.1f}s — credentials were retried after a timeout"

    def test_passphrases_never_appear_in_the_process_list(self, arista):
        """The reason credentials go through snmp.conf at all.

        Runs a walk and inspects the argv of every process while it is in
        flight; the passphrase must appear in none of them.
        """
        import subprocess
        import threading

        seen: list[str] = []
        stop = threading.Event()

        def watch():
            while not stop.is_set():
                try:
                    out = subprocess.run(["ps", "-eo", "args"], capture_output=True,
                                         text=True, timeout=5).stdout
                except (subprocess.SubprocessError, OSError):
                    return
                if "labauthpass123" in out:
                    seen.extend(
                        line for line in out.splitlines() if "labauthpass123" in line
                    )
                    return

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            with CredentialSession(LAB_CREDENTIAL) as session:
                for _ in range(4):
                    session.walk(arista.address, "1.3.6.1.2.1.2.2.1")
        finally:
            stop.set()
            watcher.join(timeout=5)

        # snmpd's own createUser line is in the emulator's config, not argv;
        # anything found here would be the scanner leaking a passphrase.
        leaks = [line for line in seen if "snmpwalk" in line or "snmpget" in line
                 or "snmpbulkwalk" in line]
        assert not leaks, f"passphrase visible in process list: {leaks}"


class TestFullScanOverTheWire:
    def test_cisco_stack_models_correctly(self, cisco_stack):
        collector = Collector([LAB_CREDENTIAL], timeout=3)
        result = build_scan_result(collector.collect(cisco_stack.address))
        assert result.is_stack
        assert len(result.devices) == 3
        assert {d.serial for d in result.devices} == {
            "FOC2530L0AB", "FOC2530L0CD", "FOC2531L0EF"
        }
        assert result.primary.software_version == "17.03.04a"
        interfaces = sum(len(d.interfaces) for d in result.devices)
        assert interfaces == 18

    def test_arista_model_is_verbatim(self, arista):
        collector = Collector([LAB_CREDENTIAL], timeout=3)
        result = build_scan_result(collector.collect(arista.address))
        assert result.primary.model == "DCS-7050SX-72Q"
        assert result.primary.manufacturer == "Arista Networks"


@pytest.mark.parametrize(
    "fixture_name,expected_version",
    [
        ("palo-pa3220", "11.1.4-h7"),
        ("fortigate-600e", "v7.2.8,build1639,240110 (GA)"),
        # Version-build, joined the way F5 names its own ISOs. This test only
        # runs where snmpd exists, which is how it stayed on the bare version
        # while the offline tests moved to version-build with the fixture.
        ("f5-bigip", "17.1.1.3-0.0.5"),
        # Version plus jumbo hotfix take; the take is a Gauge32 and this row
        # is what proves it survives a real snmpd walk, not just the fixture
        # parser.
        ("checkpoint-gaia", "R81.20 Take 89"),
        ("infoblox-nios", "9.0.4-50212"),
        ("juniper-ex4300", "21.4R3-S4.9"),
        ("aruba-7010-wlc", "8.10.0.4"),
        # ClearPass: version comes from a walked CPPM-MIB column, not a GET —
        # this row is what proves the ".*" walk path against a real snmpd.
        ("aruba-clearpass", "6.11.5.253053"),
    ],
)
def test_software_version_over_the_wire(fixture_name, expected_version):
    """Each vendor's version scalar, fetched through a real GET rather than
    replayed — this is what catches an OID that is subtly wrong."""
    port = BASE_PORT + 20 + abs(hash(fixture_name)) % 200
    with EmulatedDevice(fixture_path(fixture_name), port=port) as device:
        collector = Collector([LAB_CREDENTIAL], timeout=3)
        facts = collector.collect(device.address)
    assert facts.software_version == expected_version


class TestNeighborsOverTheWire:
    """CDP/LLDP through a real snmpd, which is where two transport bugs hid.

    The LLDP subtree lives at 1.0.8802 and is only served because the
    emulator registers a second pass_persist root for it; and binary octet
    values (a 10.x address starts with byte 0x0A — a newline; CDP capability
    words start with NULs) only survive because Hex-STRING values cross the
    line-based pass_persist protocol as type "octet" hex text, not raw
    bytes. When either regresses, rows quietly vanish — so this counts them.
    """

    def test_all_sightings_survive_the_wire(self, cisco_stack):
        collector = Collector([LAB_CREDENTIAL], timeout=3)
        facts = collector.collect(cisco_stack.address)
        assert len(facts.neighbors) == 7
        by_protocol = {}
        for neighbor in facts.neighbors:
            by_protocol.setdefault(neighbor.protocol, []).append(neighbor)
        assert len(by_protocol["lldp"]) == 5
        assert len(by_protocol["cdp"]) == 2

    def test_binary_octets_survive_the_wire(self, cisco_stack):
        """The two hazard bytes: 0x0A inside an address, 0x00 leading a MAC
        and a capability word."""
        collector = Collector([LAB_CREDENTIAL], timeout=3)
        facts = collector.collect(cisco_stack.address)
        cdp = {n.sys_name: n for n in facts.neighbors if n.protocol == "cdp"}
        assert cdp["bld-b-acc-01.example.net"].mgmt_address == "10.10.2.5"
        assert cdp["bld-b-acc-01.example.net"].capabilities_raw == "00000028"
        phone_lldp = next(n for n in facts.neighbors
                          if n.protocol == "lldp" and n.sys_name == "SEP0011223344AA")
        assert phone_lldp.port_id == "00:11:22:33:44:AA"
