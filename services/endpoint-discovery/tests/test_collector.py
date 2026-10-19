import ipaddress

import pytest

from discovery import collector as c
from discovery.schemas import Context
from snmpinv.snmp import VarBind, parse_varbinds


class Session:
    def __init__(self, lines):
        self.binds = parse_varbinds("\n".join(lines))

    def walk(self, host, oid):
        return [b for b in self.binds if b.oid.startswith(oid + ".")]


def vb(oid, key, value, kind="INTEGER"):
    return f".{oid}.{key} = {kind}: {value}"


def bridge_lines():
    return [vb(c.Q_PORT, "77.0.17.34.51.68.85", 5),
            vb(c.Q_STATUS, "77.0.17.34.51.68.85", 3),
            vb(c.Q_VLAN, "0.10", 77), vb(c.BASE_PORT, "5", 101)]


def test_qbridge_maps_fdb_id_not_assumed_vlan_and_bridge_port_not_ifindex():
    rows = c.fdb(Session(bridge_lines()), "host", Context(key="default"), {101: "Gi1"}, {101})
    assert len(rows) == 1
    assert (rows[0].mac, rows[0].vlan, rows[0].ifindex, rows[0].port_role) == ("00:11:22:33:44:55", 10, 101, "transit")


@pytest.mark.parametrize("missing", [c.Q_STATUS, c.Q_VLAN, c.BASE_PORT])
def test_partial_forwarding_table_is_not_a_success(missing):
    session = Session([line for line in bridge_lines() if not line.startswith("." + missing)])
    with pytest.raises(c.Incomplete):
        c.fdb(session, "host", Context(key="default"), {101: "Gi1"}, set())


def test_unavailable_or_empty_table_preserves_old_evidence():
    with pytest.raises(c.Unsupported):
        c.fdb(Session([]), "host", Context(key="default"), {}, set())
    with pytest.raises(c.Unsupported):
        c.neighbors(Session([]), "host", Context(key="default"), {})


def test_shared_learning_fdb_does_not_invent_a_vlan():
    session = Session(bridge_lines() + [vb(c.Q_VLAN, "0.20", 77)])
    row = c.fdb(session, "host", Context(key="default"), {101: "Gi1"}, set())[0]
    assert row.vlan == 0
    with pytest.raises(c.Incomplete):
        c.fdb(session, "host", Context(key="v10", vlan=10), {101: "Gi1"}, set())


def test_bridge_fallback_marks_vlan_unknown_and_ignores_self_entries():
    lines = [vb(c.B_PORT, "0.17.34.51.68.85", 5), vb(c.B_STATUS, "0.17.34.51.68.85", 3),
             vb(c.B_PORT, "0.17.34.51.68.86", 5), vb(c.B_STATUS, "0.17.34.51.68.86", 4),
             vb(c.BASE_PORT, "5", 101)]
    rows = c.fdb(Session(lines), "host", Context(key="default"), {101: "Gi1"}, set())
    assert len(rows) == 1 and rows[0].vlan == 0 and rows[0].port_role == "unknown"


@pytest.mark.parametrize("address", ["192.0.2.25", "2001:db8::25"])
def test_modern_neighbor_indexes_ipv4_and_ipv6_and_hex_mac(address):
    octets = ipaddress.ip_address(address).packed
    key = f"10.{1 if len(octets) == 4 else 2}.{len(octets)}." + ".".join(str(o) for o in octets)
    session = Session([vb(c.PHYS, key, "00 11 22 33 44 55", "Hex-STRING"), vb(c.PHYS_STATE, key, 1)])
    row = c.neighbors(session, "host", Context(key="default"), {10: "Vlan10"})[0]
    assert row.ip == address and row.mac == "00:11:22:33:44:55" and row.vlan == 10


def test_legacy_arp_and_incomplete_ndp():
    key = "10.192.0.2.25"
    row = c.neighbors(Session([vb(c.LEGACY, key, "00 11 22 33 44 55", "Hex-STRING"),
                               vb(c.LEGACY_TYPE, key, 3)]), "host", Context(key="default"), {10: "Vlan10"})[0]
    assert row.ip == "192.0.2.25"
    key = "10.1.4.192.0.2.25"
    rows = c.neighbors(Session([vb(c.PHYS, key, "00 00 00 00 00 00", "Hex-STRING"),
                                vb(c.PHYS_STATE, key, 7)]), "host", Context(key="default"), {10: "Vlan10"})
    assert rows == []


def test_printable_six_octet_mac_is_decoded_as_binary_address():
    assert c.hex_mac(VarBind("1", "STRING", "012345")) == "30:31:32:33:34:35"


def test_lldp_bridge_router_detection_resolves_local_id_instead_of_using_ifindex():
    session = Session([vb(c.LLDP_LOCAL, "7", '"Gi48"', "STRING"),
                       vb(c.LLDP_CAP, "0.7.1", "28 00", "Hex-STRING")])
    assert c.transit_ports(session, "host", {1048: "Gi48"}) == {1048}
    assert c.transit_ports(session, "host", {7: "DifferentPort"}) == set()


def test_credentials_cannot_downgrade_to_no_privacy():
    from discovery.schemas import Target
    target = Target(key="a", scope="a", poller="p", address="192.0.2.1", name="a")
    with pytest.raises(ValueError, match="authPriv"):
        c.collect(target, target.contexts[0], {"security_name": "netops", "auth_passphrase": "example"})
