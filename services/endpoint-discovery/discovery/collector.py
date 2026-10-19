"""Read-only standard-MIB collection using the existing net-snmp transport.

No SSH, device API, configuration engine, or SNMP SET operation is available.
An incomplete join fails the table; it must never expire previous sightings.
"""
import ipaddress
import re
import time
from uuid import uuid4

from snmpinv.snmp import Credential, CredentialSession, SnmpError

from .schemas import Entry, Report, Table, mac_address

IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
BASE_PORT = "1.3.6.1.2.1.17.1.4.1.2"
Q_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"
Q_STATUS = "1.3.6.1.2.1.17.7.1.2.2.1.3"
Q_VLAN = "1.3.6.1.2.1.17.7.1.4.2.1.3"
B_PORT = "1.3.6.1.2.1.17.4.3.1.2"
B_STATUS = "1.3.6.1.2.1.17.4.3.1.3"
PHYS = "1.3.6.1.2.1.4.35.1.4"
PHYS_STATE = "1.3.6.1.2.1.4.35.1.7"
LEGACY = "1.3.6.1.2.1.4.22.1.2"
LEGACY_TYPE = "1.3.6.1.2.1.4.22.1.4"
LLDP_LOCAL = "1.0.8802.1.1.2.1.3.7.1.3"
LLDP_CAP = "1.0.8802.1.1.2.1.4.1.1.12"


class Incomplete(ValueError):
    pass


class Unsupported(ValueError):
    pass


def column(session, host, oid):
    prefix = oid + "."
    return {v.oid[len(prefix):]: v for v in session.walk(host, oid) if v.oid.startswith(prefix)}


def integer(value):
    if value is None or value.as_int() is None:
        raise Incomplete("missing integer column")
    return value.as_int()


def hex_mac(value):
    raw = value.value.strip()
    if value.type == "Hex-STRING":
        raw = raw.replace(" ", "")
    elif value.type == "STRING" and len(value.value) == 6:
        # net-snmp prints an all-printable six-octet PhysAddress as STRING.
        raw = value.value.encode("latin1").hex()
    return mac_address(raw)


def port_names(session, host):
    names = column(session, host, IF_NAME)
    if not names:
        names = column(session, host, IF_DESCR)
    if not names:
        raise Incomplete("interface names unavailable")
    return {int(key): value.value for key, value in names.items()}


def transit_ports(session, host, names):
    # LLDP local port number is not assumed to be ifIndex. Resolve its actual
    # local port identifier against ifName/ifDescr before using capabilities.
    local = column(session, host, LLDP_LOCAL)
    capabilities = column(session, host, LLDP_CAP)
    indexes = {name: idx for idx, name in names.items()}
    transit = set()
    for key, value in capabilities.items():
        parts = key.split(".")
        if len(parts) != 3:
            continue
        port = local.get(parts[1])
        if not port or port.value not in indexes:
            continue
        # BITS: bridge(2), router(4), MSB first. Unknown encoding stays unknown.
        if value.type == "Hex-STRING":
            octets = re.split(r"[:\s]+", value.value)
            if octets and int(octets[0], 16) & 0x28:
                transit.add(indexes[port.value])
    return transit


def fdb(session, host, context, names, transit):
    ports = column(session, host, Q_PORT)
    if ports:
        statuses = column(session, host, Q_STATUS)
        vlans = column(session, host, Q_VLAN)
        by_fdb = {}
        for key, value in vlans.items():
            vlan = int(key.split(".")[-1])
            by_fdb.setdefault(integer(value), set()).add(vlan)
        mode = "q"
    else:
        ports = column(session, host, B_PORT)
        statuses = column(session, host, B_STATUS) if ports else {}
        by_fdb, mode = {}, "bridge"
    if not ports:
        # A scalar elsewhere in BRIDGE-MIB does not prove this table is in
        # the user's view. Preserve old data rather than infer disappearance.
        raise Unsupported("forwarding table empty or unavailable in this SNMP view")
    bridge = column(session, host, BASE_PORT)
    result = []
    for key, port in ports.items():
        status = integer(statuses.get(key))
        if status != 3:  # learned entries, never self/management/invalid rows
            continue
        index = integer(bridge.get(str(integer(port))))
        if index not in names:
            raise Incomplete("bridge port has no interface mapping")
        parts = [int(p) for p in key.split(".")]
        mac_parts = parts[-6:]
        if len(mac_parts) != 6 or any(not 0 <= p <= 255 for p in mac_parts):
            raise Incomplete("invalid forwarding index")
        try:
            mac = mac_address("".join(f"{p:02x}" for p in mac_parts))
        except ValueError:
            continue  # multicast/zero entries cannot identify an endpoint
        if mode == "q":
            vlan_set = by_fdb.get(parts[0])
            if not vlan_set:
                raise Incomplete("forwarding database has no VLAN mapping")
            if len(vlan_set) > 1:
                if context.vlan:
                    raise Incomplete("shared-learning FDB cannot prove the context VLAN")
                vlan_set = {0}
            elif context.vlan:
                vlan_set = vlan_set & {context.vlan}
        else:
            # BRIDGE-MIB is VLAN-unaware; unknown VLAN is explicit (0).
            vlan_set = {context.vlan}
        for vlan in sorted(vlan_set):
            result.append(Entry(mac=mac, vlan=vlan, ifindex=index, interface=names[index],
                                port_role="transit" if index in transit else "unknown"))
    return result


def neighbors(session, host, context, names):
    values = column(session, host, PHYS)
    states = column(session, host, PHYS_STATE) if values else {}
    modern = bool(values)
    if not values:
        values = column(session, host, LEGACY)
        states = column(session, host, LEGACY_TYPE) if values else {}
    if not values:
        # Cannot prove an empty neighbor table is accessible using a generic
        # scalar. Preserve the previous snapshot rather than infer absence.
        raise Unsupported("neighbor table empty or unavailable in this SNMP view")
    result = []
    for key, value in values.items():
        state = integer(states.get(key))
        if (modern and state in (5, 7)) or (not modern and state == 2):
            continue
        parts = [int(p) for p in key.split(".")]
        index = parts[0]
        if index not in names:
            raise Incomplete("neighbor has no interface mapping")
        if modern:
            address_type, size, octets = parts[1], parts[2], parts[3:]
            if address_type not in (1, 2):
                raise Incomplete("unsupported neighbor address type; configure an unzoned context")
            if size != len(octets) or size != {1: 4, 2: 16}[address_type]:
                raise Incomplete("invalid neighbor address index")
        else:
            octets = parts[1:]
            if len(octets) != 4:
                raise Incomplete("invalid IPv4 neighbor index")
        address = str(ipaddress.ip_address(bytes(octets)))
        try:
            mac = hex_mac(value)
        except ValueError:
            # Valid neighbor state with an unreadable address is partial data.
            raise Incomplete("unreadable neighbor MAC") from None
        vlan_match = re.fullmatch(r"(?:vlan|vl|irb\.)(\d+)", names[index], flags=re.IGNORECASE)
        vlan = context.vlan or (int(vlan_match[1]) if vlan_match else 0)
        result.append(Entry(mac=mac, ip=address, vlan=vlan, ifindex=index, interface=names[index]))
    return result


def collect(target, context, credentials):
    credential = Credential(name=target.credential, context=context.snmp_context, **credentials)
    # authPriv only: no authentication/privacy downgrade on fallback.
    if credential.level() != "authPriv":
        raise ValueError("SNMPv3 authPriv credentials are required")
    tables = []
    host = (f"udp6:[{target.address}]:{target.snmp_port}" if ":" in target.address
            else f"udp:{target.address}:{target.snmp_port}")
    try:
        with CredentialSession(credential, timeout=3, retries=1) as session:
            names = port_names(session, host)
            try:
                transit = transit_ports(session, host, names)
            except (SnmpError, ValueError):
                transit = set()  # missing topology never implies an access port
            for kind in context.tables:
                try:
                    entries = (fdb(session, host, context, names, transit) if kind == "fdb"
                               else neighbors(session, host, context, names))
                    tables.append(Table(kind=kind, status="complete", entries=entries))
                except Unsupported:
                    tables.append(Table(kind=kind, status="unsupported", error="table empty or unsupported in SNMP view"))
                except (SnmpError, ValueError, IndexError):
                    tables.append(Table(kind=kind, status="error", error="table collection or mapping failed"))
    except (SnmpError, ValueError):
        tables = [Table(kind=kind, status="error", error="SNMP authentication, transport, or interface collection failed")
                  for kind in context.tables]
    return Report(report_id=str(uuid4()), device_key=target.key, target_revision=target.revision, context=context.key,
                  observed_at=int(time.time()), tables=tables)
