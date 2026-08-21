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
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import mibs, vendors
from .bulkstate import GETNEXT_ONLY, BulkState
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
    software_version: str = ""

    @property
    def is_up(self) -> bool:
        return self.status == vendors.ARUBA_AP_STATUS_UP


@dataclass
class Neighbor:
    """One CDP or LLDP neighbor sighting, as the device reported it.

    This is a raw sighting, not a resolved adjacency: the same physical link
    shows up once per protocol on a Cisco box, the remote port name arrives in
    whatever form that protocol favours (CDP long, LLDP usually short), and
    nothing here has been matched against NetBox. neighbors.py does the
    dedupe, canonicalisation and classification; sync-time code does the
    matching. Keeping the sighting verbatim is what lets --probe show exactly
    what the device said when a match later looks wrong.
    """

    protocol: str                       # "lldp" or "cdp"
    local_if_index: int | None = None   # resolved; None when resolution failed
    local_port: str = ""                # local interface name, as resolved
    # How local_port/local_if_index were arrived at — LLDP's local port number
    # is an index into lldpLocPortTable, not an ifIndex, and the report should
    # say which route resolved it (or that only the unreliable fallback was
    # available) rather than presenting every resolution as equally solid.
    local_port_source: str = ""
    local_port_num: int | None = None   # LLDP lldpRemLocalPortNum, verbatim
    chassis_id_subtype: int = 0         # LLDP LldpChassisIdSubtype; 0 for CDP
    chassis_id: str = ""                # decoded per subtype (MAC-formatted when MAC)
    port_id_subtype: int = 0            # LLDP LldpPortIdSubtype; 0 for CDP
    port_id: str = ""                   # remote port, exactly as reported
    port_desc: str = ""
    sys_name: str = ""                  # LLDP sysName / CDP deviceId
    sys_desc: str = ""                  # LLDP sysDesc / CDP version paragraph
    platform: str = ""                  # CDP cdpCachePlatform; LLDP has none
    capabilities: frozenset = frozenset()   # decoded names, e.g. {"bridge","router"}
    capabilities_raw: str = ""          # verbatim hex, so undecoded bits stay visible
    mgmt_address: str = ""              # CDP management address when it is IPv4


@dataclass
class DeviceFacts:
    """Everything one device told us about itself."""

    host: str
    credential_name: str = ""
    # When the device was actually walked, not when the result was written.
    # A scan of a large fleet takes a while and its results may be pushed later
    # still, so stamping at collection is what makes a stale reading legible as
    # stale rather than looking as fresh as the run that reported it.
    collected_at: datetime | None = None
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
    neighbors: list[Neighbor] = field(default_factory=list)

    software_version: str = ""
    vendor_serial: str = ""
    vendor_model: str = ""
    vendor_part_number: str = ""
    # Every vendor scalar that was asked for, and what it returned. None means
    # the device had no such object. Diagnostic only — nothing reads it to
    # decide anything, it exists so --probe can explain an empty version.
    vendor_scalars: dict = field(default_factory=dict)
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


class _CredentialRejected(Exception):
    """This credential set was refused; try the next one.

    Internal to Collector.collect. A plain exception rather than a return
    value so the scan-with-one-credential path can be its own function and
    still let collect() own the loop.
    """

    def __init__(self, cause: SnmpError):
        super().__init__(str(cause))
        self.cause = cause


class Collector:
    """Walks one host with the first credential set that authenticates."""

    def __init__(self, credentials: list[Credential], timeout: int = 5, retries: int = 1,
                 use_bulk: bool = True, collect_interfaces: bool = True,
                 collect_ips: bool = True, collect_neighbors: bool = True,
                 max_repetitions: int = 25,
                 bulk_state: BulkState | None = None):
        if not credentials:
            raise ValueError("at least one SNMPv3 credential set is required")
        self.credentials = credentials
        self.timeout = timeout
        self.retries = retries
        self.use_bulk = use_bulk
        self.collect_interfaces = collect_interfaces
        self.collect_ips = collect_ips
        self.collect_neighbors = collect_neighbors
        self.max_repetitions = max_repetitions
        # Devices that cannot answer a full-size GETBULK are remembered, so the
        # timeouts that discover the limit are paid once rather than every run.
        self.bulk_state = bulk_state if bulk_state is not None else BulkState()

    def _session_for(self, credential: Credential, host: str) -> CredentialSession:
        """Open a session already tuned to what this host managed last time."""
        limit = self.bulk_state.limit_for(host, self.max_repetitions)
        return CredentialSession(
            credential,
            timeout=self.timeout,
            retries=self.retries,
            # A remembered GETNEXT_ONLY skips straight past GETBULK, which is
            # the whole point: no timeout is paid to rediscover it.
            use_bulk=self.use_bulk and limit != GETNEXT_ONLY,
            max_repetitions=limit or self.max_repetitions,
        )

    def collect(self, host: str) -> DeviceFacts:
        """Walk `host`, trying each credential set in order.

        Raises SnmpTimeoutError if the host never answers with any credential,
        SnmpAuthError if it answered but rejected every set.
        """
        last_auth_error: SnmpError | None = None
        for credential in self.credentials:
            session = self._session_for(credential, host)
            with session:
                try:
                    return self._try_credential(session, host, credential)
                except _CredentialRejected as exc:
                    last_auth_error = exc.cause
                    continue
                finally:
                    # Outside every failure path on purpose. Whatever GETBULK
                    # size this device settled on was measured at the cost of a
                    # timeout per step, and that measurement is just as true
                    # when the scan then failed for some other reason — which
                    # is exactly when it is most worth not paying twice.
                    if session.answered:
                        self.bulk_state.remember(
                            host, session.settled_repetitions(), self.max_repetitions
                        )

        raise last_auth_error or SnmpAuthError(f"{host}: no credential set was accepted")

    def _try_credential(self, session: CredentialSession, host: str,
                        credential: Credential) -> DeviceFacts:
        """Scan `host` with one credential set, or say it was not accepted."""
        try:
            # A single GET before any walk. It is one small packet each way, so
            # unlike a GETBULK it cannot fail because a reply was too big to
            # survive the path — which means a timeout here really does mean
            # the host is silent, and a timeout on a later walk really does
            # mean GETBULK is the problem. It also rejects a wrong credential
            # set after one packet rather than after a whole walk.
            session.probe(host)
            system = session.walk(host, mibs.SYSTEM_GROUP)
        except SnmpAuthError as exc:
            # Wrong user or passphrase for this device — the next set may be
            # the right one.
            log.debug("%s rejected credential set %r: %s", host, credential.name, exc)
            raise _CredentialRejected(exc) from exc
        # A timeout is deliberately not caught: it is silence rather than
        # rejection, and trying more credentials just multiplies the wait.

        if not system:
            raise _CredentialRejected(SnmpAuthError(f"{host}: empty system group"))
        log.info("%s authenticated with credential set %r", host, credential.name)
        return self._collect_with(session, host, credential, system)

    def _collect_with(self, session: CredentialSession, host: str, credential: Credential,
                      system: list[VarBind]) -> DeviceFacts:
        facts = DeviceFacts(
            host=host,
            credential_name=credential.name,
            collected_at=datetime.now(timezone.utc),
        )
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

        if self.collect_neighbors:
            # LLDP is IEEE-standard and tried on everything: a device without
            # it answers "nothing in that subtree" in one round trip. CDP is
            # gated on the vendor like the stackwise table — and Cisco boxes
            # typically run both, so the same physical link is expected to
            # show up twice here; neighbors.py deduplicates the sightings.
            facts.neighbors = _walk_lldp_neighbors(session, host, facts.interfaces)
            if profile is not None and profile.name == "cisco":
                facts.neighbors += _walk_cdp_neighbors(session, host, facts.interfaces)

        if profile is not None:
            _apply_vendor_scalars(session, host, facts, profile)
            if not facts.vendor_model:
                # Palo Alto, Fortinet and Opengear name the model in sysDescr
                # and publish no model scalar to read it from.
                facts.vendor_model = vendors.extract_model(facts.sys_descr, profile.model_patterns)
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
        prefix_length = _usable_prefix_length(address, prefix_length)
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
        prefix_length = _usable_prefix_length(address, prefix_length)
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


# --- CDP / LLDP neighbors ---------------------------------------------------


@dataclass
class _LocPort:
    """One lldpLocPortTable row: what a lldpRemLocalPortNum points at."""

    subtype: int
    port_id: str
    descr: str


def _walk_lldp_neighbors(session: CredentialSession, host: str,
                         interfaces: dict[int, Interface]) -> list[Neighbor]:
    """LLDP-MIB lldpRemTable — every neighbor this device has heard.

    The row index is <timeMark>.<localPortNum>.<remIndex>. localPortNum is an
    index into lldpLocPortTable, NOT an ifIndex — the MIB is explicit that the
    association goes through that table — so the local table is walked first
    and each neighbor's local port resolved through it. Only when a device
    serves no local table at all is the port number tried as an ifIndex, which
    is the equality most platforms happen to implement but none promise, and
    the sighting records which route was taken so the report can say.
    """
    try:
        binds = session.walk(host, mibs.LLDP_REM_ENTRY)
    except SnmpError as exc:
        log.debug("%s: lldpRemTable unavailable (%s)", host, exc)
        return []
    if not binds:
        return []

    loc_ports = _walk_lldp_local_ports(session, host)
    by_name, by_descr = _interface_lookups(interfaces)

    chassis_subtypes = collect_column(binds, mibs.LLDP_REM_CHASSIS_ID_SUBTYPE)
    chassis_ids = collect_column(binds, mibs.LLDP_REM_CHASSIS_ID)
    port_subtypes = collect_column(binds, mibs.LLDP_REM_PORT_ID_SUBTYPE)
    port_ids = collect_column(binds, mibs.LLDP_REM_PORT_ID)
    port_descs = collect_column(binds, mibs.LLDP_REM_PORT_DESC)
    sys_names = collect_column(binds, mibs.LLDP_REM_SYS_NAME)
    sys_descs = collect_column(binds, mibs.LLDP_REM_SYS_DESC)
    caps_enabled = collect_column(binds, mibs.LLDP_REM_SYS_CAP_ENABLED)
    caps_supported = collect_column(binds, mibs.LLDP_REM_SYS_CAP_SUPPORTED)

    neighbors = []
    for row_index in sorted(set(chassis_ids) | set(port_ids) | set(sys_names),
                            key=_row_key):
        parts = row_index.split(".")
        if len(parts) != 3:
            continue
        try:
            port_num = int(parts[1])
        except ValueError:
            continue
        if_index, local_name, source = _resolve_lldp_local_port(
            port_num, loc_ports, interfaces, by_name, by_descr
        )
        chassis_subtype = _int(chassis_subtypes.get(row_index), 0)
        port_subtype = _int(port_subtypes.get(row_index), 0)
        # Enabled capabilities are what the neighbor is doing; supported is
        # what it could do. Classification wants the former, but plenty of
        # gear sends only the latter, so fall back rather than losing the
        # signal entirely.
        caps_bind = caps_enabled.get(row_index) or caps_supported.get(row_index)
        caps_bytes = _octets(caps_bind)
        neighbors.append(Neighbor(
            protocol="lldp",
            local_if_index=if_index,
            local_port=local_name,
            local_port_source=source,
            local_port_num=port_num,
            chassis_id_subtype=chassis_subtype,
            chassis_id=_decode_lldp_chassis_id(chassis_subtype, chassis_ids.get(row_index)),
            port_id_subtype=port_subtype,
            port_id=_decode_lldp_port_id(port_subtype, port_ids.get(row_index)),
            port_desc=_printable(port_descs.get(row_index)),
            sys_name=_printable(sys_names.get(row_index)),
            sys_desc=_printable(sys_descs.get(row_index)),
            capabilities=_lldp_capability_names(caps_bytes),
            capabilities_raw=caps_bytes.hex(),
        ))
    return neighbors


def _walk_lldp_local_ports(session: CredentialSession, host: str) -> dict[int, _LocPort]:
    try:
        binds = session.walk(host, mibs.LLDP_LOC_PORT_ENTRY)
    except SnmpError as exc:
        log.debug("%s: lldpLocPortTable unavailable (%s)", host, exc)
        return {}
    subtypes = collect_column(binds, mibs.LLDP_LOC_PORT_ID_SUBTYPE)
    ids = collect_column(binds, mibs.LLDP_LOC_PORT_ID)
    descrs = collect_column(binds, mibs.LLDP_LOC_PORT_DESC)
    out: dict[int, _LocPort] = {}
    for key in set(subtypes) | set(ids) | set(descrs):
        if not key.isdigit():
            continue
        out[int(key)] = _LocPort(
            subtype=_int(subtypes.get(key), 0),
            port_id=_printable(ids.get(key)),
            descr=_printable(descrs.get(key)),
        )
    return out


def _resolve_lldp_local_port(port_num: int, loc_ports: dict[int, _LocPort],
                             interfaces: dict[int, Interface],
                             by_name: dict[str, int], by_descr: dict[str, int],
                             ) -> tuple[int | None, str, str]:
    """Turn a lldpRemLocalPortNum into (ifIndex, interface name, how).

    The "how" travels with the sighting: a port resolved through the local
    table is solid, one resolved by assuming portNum == ifIndex is the
    fallback the MIB warns against, and a report that cannot say which is
    which would make both look equally trustworthy.
    """
    loc = loc_ports.get(port_num)
    if loc is not None:
        if loc.subtype == mibs.LLDP_PORT_SUBTYPE_INTERFACE_NAME and loc.port_id:
            index = by_name.get(loc.port_id, by_descr.get(loc.port_id))
            return index, loc.port_id, "lldpLocPortId interfaceName"
        if (loc.subtype == mibs.LLDP_PORT_SUBTYPE_LOCAL and loc.port_id.isdigit()
                and int(loc.port_id) in interfaces):
            index = int(loc.port_id)
            return index, interfaces[index].display_name(), "lldpLocPortId local(7) as ifIndex"
        if loc.descr:
            index = by_name.get(loc.descr, by_descr.get(loc.descr))
            if index is not None:
                return index, interfaces[index].display_name(), "lldpLocPortDesc"
        if loc.port_id:
            # The table answered but nothing matches an interface we walked.
            # Keep the device's own name for the port; the report shows it.
            return None, loc.port_id, "lldpLocPortId (no interface matched)"
    if port_num in interfaces:
        return (port_num, interfaces[port_num].display_name(),
                "port number as ifIndex (no lldpLocPortTable row)")
    return None, "", "unresolved"


def _walk_cdp_neighbors(session: CredentialSession, host: str,
                        interfaces: dict[int, Interface]) -> list[Neighbor]:
    """CISCO-CDP-MIB cdpCacheTable, indexed <ifIndex>.<deviceIndex>.

    Unlike LLDP, the first index element here IS the local ifIndex (the MIB
    says so), so the local interface join is direct.
    """
    try:
        binds = session.walk(host, mibs.CDP_CACHE_ENTRY)
    except SnmpError as exc:
        log.debug("%s: cdpCacheTable unavailable (%s)", host, exc)
        return []

    device_ids = collect_column(binds, mibs.CDP_CACHE_DEVICE_ID)
    ports = collect_column(binds, mibs.CDP_CACHE_DEVICE_PORT)
    platforms = collect_column(binds, mibs.CDP_CACHE_PLATFORM)
    versions = collect_column(binds, mibs.CDP_CACHE_VERSION)
    caps = collect_column(binds, mibs.CDP_CACHE_CAPABILITIES)
    addr_types = collect_column(binds, mibs.CDP_CACHE_ADDRESS_TYPE)
    addrs = collect_column(binds, mibs.CDP_CACHE_ADDRESS)

    neighbors = []
    for row_index in sorted(set(device_ids) | set(ports), key=_row_key):
        parts = row_index.split(".")
        if len(parts) != 2:
            continue
        try:
            if_index = int(parts[0])
        except ValueError:
            continue
        caps_bytes = _octets(caps.get(row_index))
        caps_value = int.from_bytes(caps_bytes, "big") if caps_bytes else 0
        mgmt = ""
        if _int(addr_types.get(row_index), 0) == mibs.CDP_ADDRESS_TYPE_IP:
            data = _octets(addrs.get(row_index))
            if len(data) == 4:
                mgmt = ".".join(str(b) for b in data)
        iface = interfaces.get(if_index)
        neighbors.append(Neighbor(
            protocol="cdp",
            local_if_index=if_index,
            local_port=iface.display_name() if iface is not None else "",
            local_port_source="cdpCacheIfIndex",
            sys_name=_printable(device_ids.get(row_index)),
            sys_desc=_printable(versions.get(row_index)),
            port_id=_printable(ports.get(row_index)),
            platform=_printable(platforms.get(row_index)),
            capabilities=frozenset(
                name for bit, name in mibs.CDP_CAP_BIT_NAMES.items() if caps_value & bit
            ),
            capabilities_raw=caps_bytes.hex(),
            mgmt_address=mgmt,
        ))
    return neighbors


def _interface_lookups(interfaces: dict[int, Interface]) -> tuple[dict[str, int], dict[str, int]]:
    """Name -> ifIndex and descr -> ifIndex, for local-port resolution."""
    by_name: dict[str, int] = {}
    by_descr: dict[str, int] = {}
    for index, iface in interfaces.items():
        if iface.name:
            by_name.setdefault(iface.name, index)
        if iface.descr:
            by_descr.setdefault(iface.descr, index)
    return by_name, by_descr


def _row_key(row_index: str) -> tuple[int, ...]:
    """Numeric sort for multi-part row indexes; string order puts 10 before 2."""
    return tuple(int(p) for p in row_index.split(".") if p.isdigit())


def _octets(bind: VarBind | None) -> bytes:
    """The raw octets of a value, whichever way net-snmp printed it.

    A Hex-STRING arrives from the parser as colon-joined hex. A value whose
    bytes happen to be printable is printed as STRING instead — the LLDP
    capability octet 0x28 (bridge+router) is a "(" — so both forms have to
    decode to the same bytes or capability bits vanish on exactly the devices
    whose capabilities are the common ones.
    """
    if bind is None or not bind.value:
        return b""
    if bind.type == "Hex-STRING":
        try:
            return bytes(int(p, 16) for p in bind.value.split(":") if p)
        except ValueError:
            return b""
    return bind.value.encode("latin-1", "replace")


def _printable(bind: VarBind | None) -> str:
    """A text value that may have been printed as Hex-STRING.

    Agents emit octet-string columns as Hex-STRING whenever net-snmp cannot
    prove them printable — some stacks do it for every port name. A name is
    more useful as "Gi1/0/1" than "47:69:31:2F:30:2F:31", so convert when the
    bytes are clean ASCII and keep the hex form when they are not (that is
    real information: the value genuinely is binary).
    """
    if bind is None:
        return ""
    if bind.type == "Hex-STRING":
        data = _octets(bind)
        text = data.decode("ascii", errors="replace").strip("\x00").strip()
        if text and all(32 <= ord(c) < 127 for c in text):
            return text
        return bind.value
    return bind.value.strip()


def _mac_from_bytes(data: bytes) -> str:
    return ":".join(f"{b:02X}" for b in data)


def _decode_lldp_chassis_id(subtype: int, bind: VarBind | None) -> str:
    """Decode lldpRemChassisId according to its subtype column.

    The subtype is what makes the bytes meaningful: the same six octets are a
    MAC under subtype 4 and a hostname fragment under subtype 6. Guessing from
    the shape of the bytes instead of reading the subtype is how a switch
    named "AABBCC" turns into a MAC address.
    """
    if bind is None:
        return ""
    data = _octets(bind)
    if subtype == mibs.LLDP_CHASSIS_SUBTYPE_MAC and len(data) == 6:
        return _mac_from_bytes(data)
    if subtype == mibs.LLDP_CHASSIS_SUBTYPE_NETWORK_ADDRESS and data:
        # First octet is the IANA address family: 1 IPv4, 2 IPv6.
        if data[0] == 1 and len(data) == 5:
            return ".".join(str(b) for b in data[1:])
        if data[0] == 2 and len(data) == 17:
            return str(ipaddress.IPv6Address(data[1:]))
    return _printable(bind)


def _decode_lldp_port_id(subtype: int, bind: VarBind | None) -> str:
    """Decode lldpRemPortId per LldpPortIdSubtype.

    Careful: the port subtype numbering is NOT the chassis numbering —
    macAddress is 3 here and 4 there.
    """
    if bind is None:
        return ""
    data = _octets(bind)
    if subtype == mibs.LLDP_PORT_SUBTYPE_MAC and len(data) == 6:
        return _mac_from_bytes(data)
    if subtype == mibs.LLDP_PORT_SUBTYPE_NETWORK_ADDRESS and data:
        if data[0] == 1 and len(data) == 5:
            return ".".join(str(b) for b in data[1:])
    return _printable(bind)


def _lldp_capability_names(data: bytes) -> frozenset:
    """Decode a LldpSystemCapabilitiesMap BITS value.

    BITS number from the high-order bit of the first octet (RFC 2578
    §7.1.4), so bridge(2) is 0x20 of octet 0, not 0x04.
    """
    names = set()
    for byte_index, byte in enumerate(data):
        for bit in range(8):
            if byte & (0x80 >> bit):
                position = byte_index * 8 + bit
                names.add(mibs.LLDP_CAP_BIT_NAMES.get(position, f"bit{position}"))
    return frozenset(names)


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
    versions = collect_column(binds, vendors.ARUBA_AP_SW_VERSION)

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
            software_version=_text(versions.get(row_index)),
        ))
    return access_points


def _apply_vendor_scalars(session: CredentialSession, host: str, facts: DeviceFacts,
                          profile: vendors.VendorProfile) -> None:
    """Fetch the vendor's version/serial/model scalars in one GET."""
    wanted = (list(profile.version_oids) + list(profile.build_oids)
              + list(profile.serial_oids) + list(profile.model_oids)
              + list(profile.part_number_oids))
    if not wanted:
        return
    try:
        found = session.get(host, wanted)
    except SnmpError as exc:
        log.debug("%s: vendor scalars unavailable (%s)", host, exc)
        return
    # Kept so the probe can show what was asked and what came back. When a
    # platform reports no version the question is always "was the OID wrong,
    # or did the device answer nothing?", and only the raw result answers it.
    facts.vendor_scalars = {
        oid: (found[oid].value if oid in found else None) for oid in wanted
    }

    for oid in profile.version_oids:
        if oid in found and found[oid].value:
            facts.software_version = found[oid].value
            break
    # Appended rather than substituted, and only when the version itself came
    # back — a build with no version is meaningless on its own, and writing one
    # into the software version field would read as a version nobody recognises.
    for oid in profile.build_oids:
        if facts.software_version and oid in found and found[oid].value:
            build = found[oid].value.strip()
            # "0" is a real answer meaning no patch: Check Point reports take 0
            # on a GA install, and stamping "Take 0" on every unpatched gateway
            # would read as a patch level rather than the absence of one.
            if build in ("", "0"):
                break
            # Skip only when the version string already carries the build as a
            # standalone token — some platforms merge them upstream. A plain
            # substring test is wrong here: take 20 on R81.20 must still be
            # appended, and "20" is inside "R81.20" only as part of another
            # number.
            if not re.search(rf"(?<![\w.]){re.escape(build)}(?![\w.])",
                             facts.software_version):
                facts.software_version = profile.build_format.format(
                    version=facts.software_version, build=build)
            break

    for oid in profile.serial_oids:
        if oid in found and found[oid].value:
            facts.vendor_serial = found[oid].value
            break
    for oid in profile.model_oids:
        if oid in found and found[oid].value:
            facts.vendor_model = found[oid].value
            break
    for oid in profile.part_number_oids:
        if oid in found and found[oid].value:
            facts.vendor_part_number = found[oid].value
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


def _usable_prefix_length(address, prefix_length: int) -> int:
    """Demote a prefix length that makes its own address unusable.

    NetBox refuses to assign a network ID or a broadcast address to an
    interface — exempting /31 and /32, and /127 and /128 for v6, where both
    addresses are legitimate hosts. A device does not configure its own
    broadcast address on an interface, so when the address and the length
    disagree it is the length that is wrong: usually an ipAddressPrefix
    RowPointer whose last sub-identifier is not the prefix length, which is
    what we read it as. 169.254.251.255 reported as /24 is the common one.

    The address itself came straight off the device and is not in doubt, so it
    is kept as a host route rather than dropped. Losing the mask is a much
    smaller error than losing the fact that the device answers there — and
    dropping it would also mean the primary-IP match silently missed.
    """
    host_length = 32 if address.version == 4 else 128
    if prefix_length in ((31, 32) if address.version == 4 else (127, 128)):
        return prefix_length
    try:
        network = ipaddress.ip_network(f"{address}/{prefix_length}", strict=False)
    except ValueError:
        return host_length

    if address == network.network_address:
        reason = "the network ID of"
    elif address.version == 4 and address == network.broadcast_address:
        reason = "the broadcast address of"
    else:
        return prefix_length

    log.warning(
        "%s/%d is %s that prefix, which cannot be assigned to an interface — "
        "the device's reported prefix length looks wrong, recording it as /%d",
        address, prefix_length, reason, host_length,
    )
    return host_length


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
