"""Walk a device and turn the raw varbinds into structured facts.

This module knows about SNMP tables and nothing about NetBox. It answers one
question — "what does this device say it is?" — and `model.py` decides how that
maps onto NetBox objects. Keeping the two apart is what makes the parsing
testable against recorded walks with no NetBox anywhere near it.

The collection order matters for slow or busy devices: cheap scalars first, so
an unreachable or wrongly-credentialled host is abandoned before we spend a
minute pulling its interface table.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field

from . import mibs, vendors
from .snmp import (
    Credential,
    CredentialSession,
    SnmpAuthError,
    SnmpError,
    SnmpTimeoutError,
    VarBind,
    collect_column,
)

log = logging.getLogger(__name__)


@dataclass
class Entity:
    """One entPhysicalTable row."""

    index: int
    descr: str = ""
    entity_class: int = 0
    contained_in: int = 0
    parent_rel_pos: int = -1
    name: str = ""
    hardware_rev: str = ""
    firmware_rev: str = ""
    software_rev: str = ""
    serial: str = ""
    mfg_name: str = ""
    model: str = ""

    @property
    def is_chassis(self) -> bool:
        return self.entity_class == mibs.ENT_CLASS_CHASSIS

    @property
    def is_module(self) -> bool:
        return self.entity_class == mibs.ENT_CLASS_MODULE


@dataclass
class Interface:
    """One interface, merged from ifTable and ifXTable."""

    index: int
    name: str = ""
    descr: str = ""
    alias: str = ""
    if_type: int = 0
    mtu: int | None = None
    speed_mbps: int | None = None
    phys_address: str = ""
    admin_up: bool = True
    oper_up: bool = False

    def display_name(self) -> str:
        """ifName is the operational name; ifDescr is the fallback.

        ifName is what an engineer types ("Gi1/0/1"), ifDescr is often the same
        but on some platforms is a sentence. Prefer ifName, fall back rather
        than producing an unnamed interface.
        """
        return self.name or self.descr or f"ifIndex {self.index}"


@dataclass
class InterfaceIP:
    address: str
    prefix_length: int
    if_index: int

    def cidr(self) -> str:
        return f"{self.address}/{self.prefix_length}"


@dataclass
class StackMember:
    """One row of CISCO-STACKWISE-MIB cswSwitchInfoTable.

    `entity_index` is the table index, which is an entPhysicalIndex — that is
    what lets a member's switch number be joined to its own chassis entity and
    therefore to its own serial and model.
    """

    entity_index: int
    switch_number: int
    role: int = 0
    state: int = 0
    mac_address: str = ""

    @property
    def is_master(self) -> bool:
        return self.role == mibs.CSW_ROLE_MASTER

    @property
    def is_present(self) -> bool:
        """Provisioned-but-absent members must not become NetBox devices."""
        return self.state in mibs.CSW_STATES_PRESENT


@dataclass
class AccessPoint:
    """One Aruba AP as reported by its controller."""

    mac_address: str
    name: str = ""
    model: str = ""
    serial: str = ""
    ip_address: str = ""
    group: str = ""
    status: int = 0

    @property
    def is_up(self) -> bool:
        return self.status == vendors.ARUBA_AP_STATUS_UP


@dataclass
class DeviceFacts:
    """Everything one device told us about itself."""

    host: str
    credential_name: str = ""
    sys_descr: str = ""
    sys_object_id: str = ""
    sys_name: str = ""
    sys_location: str = ""
    sys_contact: str = ""
    sys_uptime: str = ""

    entities: list[Entity] = field(default_factory=list)
    interfaces: dict[int, Interface] = field(default_factory=dict)
    ips: list[InterfaceIP] = field(default_factory=list)
    stack_members: list[StackMember] = field(default_factory=list)
    access_points: list[AccessPoint] = field(default_factory=list)

    software_version: str = ""
    vendor_serial: str = ""
    vendor_model: str = ""
    profile: vendors.VendorProfile | None = None

    def chassis_entities(self) -> list[Entity]:
        return [e for e in self.entities if e.is_chassis]

    def module_entities(self) -> list[Entity]:
        return [e for e in self.entities if e.is_module]

    def entity_by_index(self, index: int) -> Entity | None:
        for entity in self.entities:
            if entity.index == index:
                return entity
        return None


class Collector:
    """Walks one host with the first credential set that authenticates."""

    def __init__(self, credentials: list[Credential], timeout: int = 5, retries: int = 1,
                 use_bulk: bool = True, collect_interfaces: bool = True,
                 collect_ips: bool = True):
        if not credentials:
            raise ValueError("at least one SNMPv3 credential set is required")
        self.credentials = credentials
        self.timeout = timeout
        self.retries = retries
        self.use_bulk = use_bulk
        self.collect_interfaces = collect_interfaces
        self.collect_ips = collect_ips

    def collect(self, host: str) -> DeviceFacts:
        """Walk `host`, trying each credential set in order.

        Raises SnmpTimeoutError if the host never answers with any credential,
        SnmpAuthError if it answered but rejected every set.
        """
        last_auth_error: SnmpError | None = None
        for credential in self.credentials:
            session = CredentialSession(
                credential, timeout=self.timeout, retries=self.retries, use_bulk=self.use_bulk
            )
            with session:
                try:
                    system = session.walk(host, mibs.SYSTEM_GROUP)
                except SnmpAuthError as exc:
                    # Wrong user or passphrase for this device — the next set
                    # may be the right one.
                    log.debug("%s rejected credential set %r: %s", host, credential.name, exc)
                    last_auth_error = exc
                    continue
                except SnmpTimeoutError:
                    # Silence, not rejection. More credentials will not help and
                    # each one costs another full timeout.
                    raise
                if not system:
                    last_auth_error = SnmpAuthError(f"{host}: empty system group")
                    continue
                log.info("%s authenticated with credential set %r", host, credential.name)
                return self._collect_with(session, host, credential, system)

        raise last_auth_error or SnmpAuthError(f"{host}: no credential set was accepted")

    def _collect_with(self, session: CredentialSession, host: str, credential: Credential,
                      system: list[VarBind]) -> DeviceFacts:
        facts = DeviceFacts(host=host, credential_name=credential.name)
        _apply_system_group(facts, system)

        facts.profile = vendors.profile_for_sysobjectid(facts.sys_object_id)
        profile = facts.profile

        facts.entities = _walk_entities(session, host)

        if self.collect_interfaces:
            facts.interfaces = _walk_interfaces(session, host)
        if self.collect_ips:
            facts.ips = _walk_ips(session, host)

        # Cisco stacks. Walking this on a non-Cisco device is a wasted round
        # trip, so it is gated on the vendor rather than attempted blindly.
        if profile is not None and profile.name == "cisco":
            facts.stack_members = _walk_stack_members(session, host)

        if profile is not None:
            _apply_vendor_scalars(session, host, facts, profile)
            if profile.name == "aruba":
                facts.access_points = _walk_access_points(session, host)

        # sysDescr is the fallback for every vendor whose version scalar was
        # absent, and the only source for the ones that publish none.
        if not facts.software_version:
            patterns = profile.version_patterns if profile else ()
            facts.software_version = vendors.extract_version(facts.sys_descr, patterns)
        if not facts.software_version:
            # Some platforms leave sysDescr terse but fill in the chassis
            # entity's software rev.
            for entity in facts.chassis_entities():
                if entity.software_rev:
                    facts.software_version = entity.software_rev
                    break

        return facts


def _apply_system_group(facts: DeviceFacts, binds: list[VarBind]) -> None:
    by_oid = {bind.oid: bind for bind in binds}
    facts.sys_descr = _text(by_oid.get(mibs.SYS_DESCR))
    facts.sys_object_id = _text(by_oid.get(mibs.SYS_OBJECT_ID))
    facts.sys_uptime = _text(by_oid.get(mibs.SYS_UPTIME))
    facts.sys_contact = _text(by_oid.get(mibs.SYS_CONTACT))
    facts.sys_name = _text(by_oid.get(mibs.SYS_NAME))
    facts.sys_location = _text(by_oid.get(mibs.SYS_LOCATION))


def _walk_entities(session: CredentialSession, host: str) -> list[Entity]:
    """Read entPhysicalTable — the device's own account of its hardware."""
    try:
        binds = session.walk(host, mibs.ENT_PHYSICAL_TABLE)
    except SnmpError as exc:
        log.debug("%s: entPhysicalTable unavailable (%s)", host, exc)
        return []

    columns = {
        "descr": collect_column(binds, mibs.ENT_DESCR),
        "contained_in": collect_column(binds, mibs.ENT_CONTAINED_IN),
        "entity_class": collect_column(binds, mibs.ENT_CLASS),
        "parent_rel_pos": collect_column(binds, mibs.ENT_PARENT_REL_POS),
        "name": collect_column(binds, mibs.ENT_NAME),
        "hardware_rev": collect_column(binds, mibs.ENT_HARDWARE_REV),
        "firmware_rev": collect_column(binds, mibs.ENT_FIRMWARE_REV),
        "software_rev": collect_column(binds, mibs.ENT_SOFTWARE_REV),
        "serial": collect_column(binds, mibs.ENT_SERIAL_NUM),
        "mfg_name": collect_column(binds, mibs.ENT_MFG_NAME),
        "model": collect_column(binds, mibs.ENT_MODEL_NAME),
    }
    indexes = sorted(
        {int(i) for column in columns.values() for i in column if i.isdigit()}
    )
    entities = []
    for index in indexes:
        key = str(index)
        entities.append(Entity(
            index=index,
            descr=_text(columns["descr"].get(key)),
            entity_class=_int(columns["entity_class"].get(key), 0),
            contained_in=_int(columns["contained_in"].get(key), 0),
            parent_rel_pos=_int(columns["parent_rel_pos"].get(key), -1),
            name=_text(columns["name"].get(key)),
            hardware_rev=_text(columns["hardware_rev"].get(key)),
            firmware_rev=_text(columns["firmware_rev"].get(key)),
            software_rev=_text(columns["software_rev"].get(key)),
            serial=_text(columns["serial"].get(key)),
            mfg_name=_text(columns["mfg_name"].get(key)),
            model=_text(columns["model"].get(key)),
        ))
    return entities


def _walk_interfaces(session: CredentialSession, host: str) -> dict[int, Interface]:
    """Merge ifTable and ifXTable into one interface per ifIndex.

    ifXTable is walked first because ifName and ifAlias live only there; a
    device that does not implement it still gets everything from ifTable.
    """
    interfaces: dict[int, Interface] = {}

    try:
        x_binds = session.walk(host, mibs.IF_X_TABLE)
    except SnmpError as exc:
        log.debug("%s: ifXTable unavailable (%s)", host, exc)
        x_binds = []

    names = collect_column(x_binds, mibs.IF_NAME)
    high_speeds = collect_column(x_binds, mibs.IF_HIGH_SPEED)
    aliases = collect_column(x_binds, mibs.IF_ALIAS)

    try:
        binds = session.walk(host, mibs.IF_TABLE)
    except SnmpError as exc:
        log.warning("%s: ifTable unavailable (%s)", host, exc)
        binds = []

    descrs = collect_column(binds, mibs.IF_DESCR)
    types = collect_column(binds, mibs.IF_TYPE)
    mtus = collect_column(binds, mibs.IF_MTU)
    speeds = collect_column(binds, mibs.IF_SPEED)
    phys = collect_column(binds, mibs.IF_PHYS_ADDRESS)
    admin = collect_column(binds, mibs.IF_ADMIN_STATUS)
    oper = collect_column(binds, mibs.IF_OPER_STATUS)

    all_indexes = set(descrs) | set(types) | set(names)
    for key in all_indexes:
        if not key.isdigit():
            continue
        index = int(key)
        speed_mbps = _int(high_speeds.get(key), None)
        if speed_mbps is None:
            # ifSpeed is in bits/sec and caps out at ~4.29 Gbps, which is why
            # ifHighSpeed exists. Only fall back to it when ifXTable is absent.
            raw_speed = _int(speeds.get(key), None)
            speed_mbps = raw_speed // 1_000_000 if raw_speed else None
        interfaces[index] = Interface(
            index=index,
            name=_text(names.get(key)),
            descr=_text(descrs.get(key)),
            alias=_text(aliases.get(key)),
            if_type=_int(types.get(key), 0),
            mtu=_int(mtus.get(key), None),
            speed_mbps=speed_mbps,
            phys_address=_text(phys.get(key)),
            admin_up=_int(admin.get(key), mibs.IF_ADMIN_UP) == mibs.IF_ADMIN_UP,
            oper_up=_int(oper.get(key), 0) == mibs.IF_OPER_UP,
        )
    return interfaces


def _walk_ips(session: CredentialSession, host: str) -> list[InterfaceIP]:
    """Read interface addresses, preferring the current table over the legacy one."""
    ips = _walk_ip_address_table(session, host)
    if ips:
        return ips
    return _walk_legacy_ip_addr_table(session, host)


def _walk_ip_address_table(session: CredentialSession, host: str) -> list[InterfaceIP]:
    """IP-MIB ipAddressTable (1.3.6.1.2.1.4.34).

    The prefix length is not a column: it is the last sub-identifier of the
    RowPointer in ipAddressPrefix, which points into ipAddressPrefixTable. That
    is awkward but it is where the number lives.
    """
    try:
        binds = session.walk(host, mibs.IP_ADDRESS_TABLE)
    except SnmpError as exc:
        log.debug("%s: ipAddressTable unavailable (%s)", host, exc)
        return []

    if_indexes = collect_column(binds, mibs.IP_ADDRESS_IF_INDEX)
    prefixes = collect_column(binds, mibs.IP_ADDRESS_PREFIX)

    out: list[InterfaceIP] = []
    for row_index, bind in if_indexes.items():
        address = _decode_inet_address(row_index)
        if address is None:
            continue
        if_index = bind.as_int()
        if if_index is None:
            continue
        prefix_length = None
        pointer = prefixes.get(row_index)
        if pointer is not None and pointer.value:
            prefix_length = _prefix_length_from_pointer(pointer.value)
        if prefix_length is None:
            prefix_length = 32 if address.version == 4 else 128
        out.append(InterfaceIP(str(address), prefix_length, if_index))
    return out


def _walk_legacy_ip_addr_table(session: CredentialSession, host: str) -> list[InterfaceIP]:
    """Deprecated IPv4-only ipAddrTable (1.3.6.1.2.1.4.20)."""
    try:
        binds = session.walk(host, mibs.IP_ADDR_TABLE)
    except SnmpError as exc:
        log.debug("%s: ipAddrTable unavailable (%s)", host, exc)
        return []

    if_indexes = collect_column(binds, mibs.IP_AD_ENT_IF_INDEX)
    netmasks = collect_column(binds, mibs.IP_AD_ENT_NETMASK)

    out: list[InterfaceIP] = []
    for row_index, bind in if_indexes.items():
        # The row index is the dotted IPv4 address itself.
        try:
            address = ipaddress.IPv4Address(row_index)
        except ValueError:
            continue
        if_index = bind.as_int()
        if if_index is None:
            continue
        prefix_length = 32
        mask_bind = netmasks.get(row_index)
        if mask_bind is not None and mask_bind.value:
            try:
                prefix_length = ipaddress.IPv4Network(f"0.0.0.0/{mask_bind.value}").prefixlen
            except ValueError:
                pass
        out.append(InterfaceIP(str(address), prefix_length, if_index))
    return out


def _walk_stack_members(session: CredentialSession, host: str) -> list[StackMember]:
    """CISCO-STACKWISE-MIB cswSwitchInfoTable, indexed by entPhysicalIndex."""
    try:
        binds = session.walk(host, mibs.CSW_SWITCH_INFO_TABLE)
    except SnmpError as exc:
        log.debug("%s: stackwise MIB unavailable (%s)", host, exc)
        return []

    numbers = collect_column(binds, mibs.CSW_SWITCH_NUM_CURRENT)
    roles = collect_column(binds, mibs.CSW_SWITCH_ROLE)
    states = collect_column(binds, mibs.CSW_SWITCH_STATE)
    macs = collect_column(binds, mibs.CSW_SWITCH_MAC_ADDRESS)

    members = []
    for key, bind in sorted(numbers.items(), key=lambda kv: _int_key(kv[0])):
        if not key.isdigit():
            continue
        switch_number = bind.as_int()
        if switch_number is None:
            continue
        members.append(StackMember(
            entity_index=int(key),
            switch_number=switch_number,
            role=_int(roles.get(key), 0),
            state=_int(states.get(key), 0),
            mac_address=_text(macs.get(key)),
        ))
    return members


def _walk_access_points(session: CredentialSession, host: str) -> list[AccessPoint]:
    """Aruba controllers know every AP they terminate; ask them for the list.

    The APs themselves are usually not directly pollable — they tunnel to the
    controller and often sit on networks the poller cannot reach — so the
    controller's own table is the only practical way to inventory them.
    """
    try:
        binds = session.walk(host, vendors.ARUBA_AP_ENTRY)
    except SnmpError as exc:
        log.debug("%s: Aruba AP table unavailable (%s)", host, exc)
        return []

    names = collect_column(binds, vendors.ARUBA_AP_NAME)
    models = collect_column(binds, vendors.ARUBA_AP_MODEL_NAME)
    serials = collect_column(binds, vendors.ARUBA_AP_SERIAL)
    ips = collect_column(binds, vendors.ARUBA_AP_IP)
    groups = collect_column(binds, vendors.ARUBA_AP_GROUP)
    statuses = collect_column(binds, vendors.ARUBA_AP_STATUS)

    access_points = []
    for row_index in sorted(set(names) | set(serials) | set(models)):
        mac = _decode_mac_index(row_index)
        if mac is None:
            continue
        access_points.append(AccessPoint(
            mac_address=mac,
            name=_text(names.get(row_index)),
            model=_text(models.get(row_index)),
            serial=_text(serials.get(row_index)),
            ip_address=_text(ips.get(row_index)),
            group=_text(groups.get(row_index)),
            status=_int(statuses.get(row_index), 0),
        ))
    return access_points


def _apply_vendor_scalars(session: CredentialSession, host: str, facts: DeviceFacts,
                          profile: vendors.VendorProfile) -> None:
    """Fetch the vendor's version/serial/model scalars in one GET."""
    wanted = list(profile.version_oids) + list(profile.serial_oids) + list(profile.model_oids)
    if not wanted:
        return
    try:
        found = session.get(host, wanted)
    except SnmpError as exc:
        log.debug("%s: vendor scalars unavailable (%s)", host, exc)
        return
    for oid in profile.version_oids:
        if oid in found and found[oid].value:
            facts.software_version = found[oid].value
            break
    for oid in profile.serial_oids:
        if oid in found and found[oid].value:
            facts.vendor_serial = found[oid].value
            break
    for oid in profile.model_oids:
        if oid in found and found[oid].value:
            facts.vendor_model = found[oid].value
            break


# --- index decoding ---------------------------------------------------------


def _decode_inet_address(row_index: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Decode an ipAddressTable row index: `<addrType>.<len>.<byte>...`."""
    parts = row_index.split(".")
    if len(parts) < 3:
        return None
    try:
        addr_type = int(parts[0])
        length = int(parts[1])
        octets = [int(p) for p in parts[2:2 + length]]
    except ValueError:
        return None
    if len(octets) != length:
        return None
    try:
        if addr_type == mibs.INET_TYPE_IPV4 and length == 4:
            return ipaddress.IPv4Address(bytes(octets))
        if addr_type == mibs.INET_TYPE_IPV6 and length == 16:
            return ipaddress.IPv6Address(bytes(octets))
    except (ValueError, ipaddress.AddressValueError):
        return None
    return None


def _prefix_length_from_pointer(pointer: str) -> int | None:
    """The last sub-identifier of an ipAddressPrefix RowPointer is the length."""
    parts = pointer.strip().lstrip(".").split(".")
    if not parts:
        return None
    try:
        value = int(parts[-1])
    except ValueError:
        return None
    # A zeroDotZero pointer means "unknown"; so does an implausible length.
    return value if 0 < value <= 128 else None


def _decode_mac_index(row_index: str) -> str | None:
    """Decode a 6-part decimal OID index into a MAC address."""
    parts = row_index.split(".")
    if len(parts) != 6:
        return None
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return None
    if any(o < 0 or o > 255 for o in octets):
        return None
    return ":".join(f"{o:02X}" for o in octets)


def _text(bind: VarBind | None) -> str:
    return bind.value.strip() if bind is not None and bind.value else ""


def _int(bind: VarBind | None, default):
    if bind is None:
        return default
    return bind.as_int(default)


def _int_key(key: str) -> int:
    return int(key) if key.isdigit() else 0
