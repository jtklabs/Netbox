"""Turn collected SNMP facts into the shape NetBox stores.

The interesting decisions all live here:

  * A Cisco stack is several NetBox Devices in one VirtualChassis, not one
    Device with a pile of modules. Each member has its own serial and its own
    model, because each member is a separate box that can be RMA'd on its own.
  * Interfaces are attached to the member that physically owns the port, which
    on Cisco is encoded in the interface name (`GigabitEthernet2/0/1` is on
    member 2).
  * Model and manufacturer come from what the device reported, never from a
    lookup keyed on sysObjectID. Where ENTITY-MIB is empty — which is normal
    for firewalls and load balancers — the vendor's own scalar OIDs are used
    instead, and if neither answered we leave the model blank rather than
    inventing one.

Nothing here talks to NetBox; `sync.py` does the writing. That split keeps this
logic testable against recorded walks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import mibs, vendors
from .collect import DeviceFacts, Entity, Interface

log = logging.getLogger(__name__)

# `GigabitEthernet2/0/1` / `Te2/1/4` — on a stacked platform the first number
# is the switch number. Requiring all three parts matters: a two-part name like
# `GigabitEthernet0/1` is a fixed-config switch's port numbering and its first
# number is a slot, not a stack member.
_STACK_PORT = re.compile(r"^[A-Za-z][A-Za-z\-]*(\d+)/\d+/\d+(?:\.\d+)?$")


@dataclass
class ModuleRecord:
    bay_name: str
    model: str
    serial: str = ""
    manufacturer: str = ""
    description: str = ""


@dataclass
class InterfaceRecord:
    name: str
    type_slug: str
    enabled: bool = True
    mtu: int | None = None
    mac_address: str = ""
    description: str = ""
    speed_kbps: int | None = None
    ip_addresses: list[str] = field(default_factory=list)


@dataclass
class DeviceRecord:
    """One NetBox Device — a standalone box, or one member of a stack."""

    name: str
    serial: str = ""
    model: str = ""
    manufacturer: str = ""
    platform: str = ""
    software_version: str = ""
    description: str = ""
    vc_position: int | None = None
    vc_is_master: bool = False
    interfaces: list[InterfaceRecord] = field(default_factory=list)
    modules: list[ModuleRecord] = field(default_factory=list)
    # Set for APs learned from a controller; they are created as devices but
    # are never scanned directly and have no interfaces of their own.
    is_access_point: bool = False


@dataclass
class ScanResult:
    """Everything one scanned host turns into."""

    host: str
    sys_name: str = ""
    devices: list[DeviceRecord] = field(default_factory=list)
    virtual_chassis_name: str = ""
    access_points: list[DeviceRecord] = field(default_factory=list)
    credential_name: str = ""
    facts: DeviceFacts | None = None

    @property
    def is_stack(self) -> bool:
        return bool(self.virtual_chassis_name) and len(self.devices) > 1

    @property
    def primary(self) -> DeviceRecord | None:
        """The device the scanned IP belongs to — the stack master, or the box."""
        for device in self.devices:
            if device.vc_is_master:
                return device
        return self.devices[0] if self.devices else None


def build_scan_result(facts: DeviceFacts, ap_role_enabled: bool = True) -> ScanResult:
    """Model one device's facts into NetBox-shaped records."""
    result = ScanResult(
        host=facts.host,
        sys_name=facts.sys_name,
        credential_name=facts.credential_name,
        facts=facts,
    )

    manufacturer = _manufacturer(facts)
    platform = vendors.platform_for(facts.profile, facts.sys_descr)
    base_name = _device_name(facts)

    members = _stack_members(facts)
    if len(members) > 1:
        result.virtual_chassis_name = base_name
        result.devices = _build_stack_devices(facts, members, base_name, manufacturer, platform)
    else:
        result.devices = [_build_single_device(facts, base_name, manufacturer, platform)]

    _attach_interfaces(facts, result)
    _attach_modules(facts, result, manufacturer)

    if ap_role_enabled and facts.access_points:
        result.access_points = _build_access_points(facts)

    return result


# --- naming and identity ----------------------------------------------------


def _device_name(facts: DeviceFacts) -> str:
    """Use the device's own hostname, trimmed of any DNS suffix.

    Falls back to the polled address so a device with no sysName still lands in
    NetBox under something an operator can recognise.
    """
    name = (facts.sys_name or "").strip()
    if not name:
        return facts.host
    # sysName is frequently an FQDN; NetBox device names are conventionally the
    # short name, and the domain lives on the VirtualChassis or the site.
    return name.split(".")[0]


def _manufacturer(facts: DeviceFacts) -> str:
    """Prefer what the hardware says, then the vendor profile, then the OID map."""
    for entity in facts.chassis_entities():
        if entity.mfg_name:
            return _tidy_manufacturer(entity.mfg_name)
    if facts.profile is not None:
        return facts.profile.manufacturer
    enterprise = vendors.enterprise_number(facts.sys_object_id)
    if enterprise is not None:
        return mibs.ENTERPRISE_MANUFACTURERS.get(enterprise, "")
    return ""


def _tidy_manufacturer(raw: str) -> str:
    """Normalise the noisier entPhysicalMfgName values.

    Devices report anything from "Cisco" to "Cisco Systems, Inc." for the same
    vendor, and NetBox keys manufacturers by name, so without this one fleet
    ends up with three Cisco manufacturers and device types split across them.
    """
    cleaned = raw.strip().strip(",")
    lowered = cleaned.lower()
    for needle, canonical in (
        ("cisco", "Cisco"),
        ("arista", "Arista Networks"),
        ("aruba", "Aruba Networks"),
        ("hewlett", "HPE"),
        ("hpe", "HPE"),
        ("juniper", "Juniper Networks"),
        ("palo alto", "Palo Alto Networks"),
        ("fortinet", "Fortinet"),
        ("check point", "Check Point"),
        ("checkpoint", "Check Point"),
        ("infoblox", "Infoblox"),
        ("f5 ", "F5 Networks"),
        ("opengear", "Opengear"),
    ):
        if needle in lowered:
            return canonical
    return cleaned


def _model_for(facts: DeviceFacts, entity: Entity | None) -> str:
    """The chassis' own model name, with vendor scalars as the fallback.

    Deliberately never derived from sysObjectID. If nothing reported a model we
    return "" and the sync layer refuses to invent a device type — a missing
    model is a visible gap, a guessed one is a silent wrong answer that somebody
    later has to un-learn.
    """
    if entity is not None and entity.model:
        return entity.model.strip()
    if facts.vendor_model:
        return facts.vendor_model.strip()
    return ""


def _serial_for(facts: DeviceFacts, entity: Entity | None) -> str:
    if entity is not None and entity.serial:
        return entity.serial.strip()
    if facts.vendor_serial:
        return facts.vendor_serial.strip()
    return ""


# --- stacks -----------------------------------------------------------------


@dataclass
class _Member:
    """A resolved stack member: its position, its role and its chassis entity."""

    position: int
    is_master: bool
    entity: Entity | None


def _stack_members(facts: DeviceFacts) -> list[_Member]:
    """Work out the stack membership, preferring STACKWISE over ENTITY-MIB.

    cswSwitchInfoTable is indexed by entPhysicalIndex, so each row joins
    directly to the chassis entity carrying that member's serial and model.
    When the stackwise MIB is absent (non-Cisco, or a Cisco platform that does
    not implement it) we fall back to counting chassis entities, which is how a
    stacked device shows up in ENTITY-MIB regardless of vendor.
    """
    present = [m for m in facts.stack_members if m.is_present]
    if present:
        members = []
        for member in sorted(present, key=lambda m: m.switch_number):
            entity = facts.entity_by_index(member.entity_index)
            if entity is None or not entity.is_chassis:
                # The index should be a chassis entity; if the device disagrees,
                # fall back to positional matching rather than dropping a member.
                entity = _nth_chassis(facts, member.switch_number)
            members.append(_Member(member.switch_number, member.is_master, entity))
        if len(members) > 1 and not any(m.is_master for m in members):
            # A stack with no master reported is still a stack; treat the
            # lowest-numbered member as the master so NetBox has one.
            members[0] = _Member(members[0].position, True, members[0].entity)
        return members

    chassis = facts.chassis_entities()
    if len(chassis) > 1:
        ordered = sorted(chassis, key=lambda e: (e.parent_rel_pos if e.parent_rel_pos >= 0 else e.index))
        return [
            _Member(position=index + 1, is_master=(index == 0), entity=entity)
            for index, entity in enumerate(ordered)
        ]
    if chassis:
        return [_Member(position=1, is_master=True, entity=chassis[0])]
    return [_Member(position=1, is_master=True, entity=None)]


def _nth_chassis(facts: DeviceFacts, number: int) -> Entity | None:
    chassis = facts.chassis_entities()
    index = number - 1
    return chassis[index] if 0 <= index < len(chassis) else None


def _build_stack_devices(facts: DeviceFacts, members: list[_Member], base_name: str,
                         manufacturer: str, platform: str) -> list[DeviceRecord]:
    """One NetBox Device per stack member.

    Member names follow `<stack>-<n>` for everything but the master, which keeps
    the stack's own name. That way the name an operator already uses keeps
    pointing at the device that answers on the management address.
    """
    devices = []
    for member in members:
        name = base_name if member.is_master else f"{base_name}-{member.position}"
        devices.append(DeviceRecord(
            name=name,
            serial=_serial_for(facts, member.entity) if member.entity else "",
            model=_model_for(facts, member.entity),
            manufacturer=manufacturer,
            platform=platform,
            software_version=facts.software_version,
            vc_position=member.position,
            vc_is_master=member.is_master,
        ))
    return devices


def _build_single_device(facts: DeviceFacts, name: str, manufacturer: str,
                         platform: str) -> DeviceRecord:
    chassis = facts.chassis_entities()
    entity = chassis[0] if chassis else None
    return DeviceRecord(
        name=name,
        serial=_serial_for(facts, entity),
        model=_model_for(facts, entity),
        manufacturer=manufacturer,
        platform=platform,
        software_version=facts.software_version,
    )


# --- interfaces -------------------------------------------------------------


def _attach_interfaces(facts: DeviceFacts, result: ScanResult) -> None:
    """Attach each interface to the device that physically owns the port."""
    if not facts.interfaces:
        return

    ips_by_index: dict[int, list[str]] = {}
    for entry in facts.ips:
        ips_by_index.setdefault(entry.if_index, []).append(entry.cidr())

    by_position = {d.vc_position: d for d in result.devices if d.vc_position is not None}
    primary = result.primary
    if primary is None:
        return

    for index in sorted(facts.interfaces):
        interface = facts.interfaces[index]
        record = _interface_record(interface, ips_by_index.get(index, []))
        owner = primary
        if result.is_stack:
            member_number = _member_from_interface_name(record.name)
            if member_number is not None and member_number in by_position:
                owner = by_position[member_number]
        owner.interfaces.append(record)


def _interface_record(interface: Interface, ip_addresses: list[str]) -> InterfaceRecord:
    name = interface.display_name()
    return InterfaceRecord(
        name=name,
        type_slug=mibs.netbox_interface_type(interface.if_type, interface.speed_mbps, name),
        enabled=interface.admin_up,
        mtu=interface.mtu if interface.mtu and 0 < interface.mtu <= 65536 else None,
        mac_address=_normalise_mac(interface.phys_address),
        # ifAlias is the interface description an engineer configured, which is
        # exactly what NetBox's description field is for.
        description=interface.alias,
        speed_kbps=interface.speed_mbps * 1000 if interface.speed_mbps else None,
        ip_addresses=ip_addresses,
    )


def _member_from_interface_name(name: str) -> int | None:
    match = _STACK_PORT.match(name.strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _normalise_mac(raw: str) -> str:
    """Return a colon-separated MAC, or "" if this is not one.

    An all-zero address means "no MAC" on plenty of platforms — virtual
    interfaces in particular — and writing 00:00:00:00:00:00 into NetBox for
    every VLAN interface is noise, not data.
    """
    if not raw:
        return ""
    cleaned = raw.replace("-", ":").replace(".", ":").strip().upper()
    parts = [p for p in cleaned.split(":") if p]
    if len(parts) != 6:
        return ""
    if not all(re.fullmatch(r"[0-9A-F]{1,2}", p) for p in parts):
        return ""
    normalised = ":".join(p.zfill(2) for p in parts)
    if normalised == "00:00:00:00:00:00":
        return ""
    return normalised


# --- modules ----------------------------------------------------------------


def _attach_modules(facts: DeviceFacts, result: ScanResult, manufacturer: str) -> None:
    """Attach module entities to the member whose chassis contains them.

    Containment is followed upwards through containers — a module usually sits
    in a container that sits in the chassis, not directly in the chassis — so a
    naive one-level check would attach nothing on most platforms.
    """
    modules = facts.module_entities()
    if not modules:
        return

    chassis_by_position = {}
    for member in _stack_members(facts):
        if member.entity is not None:
            chassis_by_position[member.entity.index] = member.position

    by_position = {d.vc_position: d for d in result.devices if d.vc_position is not None}
    primary = result.primary
    if primary is None:
        return

    for entity in modules:
        if not entity.model and not entity.serial:
            # A module row with neither a model nor a serial carries nothing
            # worth creating in NetBox.
            continue
        owner = primary
        if result.is_stack:
            chassis_index = _containing_chassis(facts, entity)
            position = chassis_by_position.get(chassis_index)
            if position is not None and position in by_position:
                owner = by_position[position]
        owner.modules.append(ModuleRecord(
            bay_name=_module_bay_name(entity),
            model=entity.model.strip(),
            serial=entity.serial.strip(),
            manufacturer=_tidy_manufacturer(entity.mfg_name) if entity.mfg_name else manufacturer,
            description=entity.descr.strip(),
        ))


def _containing_chassis(facts: DeviceFacts, entity: Entity, max_depth: int = 12) -> int | None:
    """Walk entPhysicalContainedIn upwards until a chassis is reached."""
    seen = set()
    current = entity
    for _ in range(max_depth):
        parent_index = current.contained_in
        if not parent_index or parent_index in seen:
            return None
        seen.add(parent_index)
        parent = facts.entity_by_index(parent_index)
        if parent is None:
            return None
        if parent.is_chassis:
            return parent.index
        current = parent
    return None


def _module_bay_name(entity: Entity) -> str:
    """Name the bay the way the device names it.

    entPhysicalName is usually the slot label already ("Slot 1", "module 3"),
    which is what an engineer looking at the physical box would expect to read
    in NetBox. Fall back to the relative position when it is blank.
    """
    if entity.name:
        return entity.name.strip()
    if entity.parent_rel_pos >= 0:
        return f"Slot {entity.parent_rel_pos}"
    return f"Slot {entity.index}"


# --- access points ----------------------------------------------------------


def _build_access_points(facts: DeviceFacts) -> list[DeviceRecord]:
    """Model each AP a controller reported as its own NetBox Device."""
    records = []
    for access_point in facts.access_points:
        if not access_point.is_up and not access_point.serial:
            # An AP that is down and has never reported a serial is a stale
            # controller entry, not hardware we can inventory.
            continue
        name = access_point.name or access_point.mac_address.replace(":", "").lower()
        records.append(DeviceRecord(
            name=name,
            serial=access_point.serial,
            model=access_point.model,
            manufacturer="Aruba Networks",
            platform="ArubaOS",
            # Campus APs do not hold their own image: the controller pushes it,
            # and every AP it terminates runs the version the controller runs.
            # The AP table reports no version of its own, so inheriting the
            # controller's is the only way these get one at all, and it is
            # right except in the minutes mid-upgrade when APs are reloading in
            # batches. A version that is briefly stale beats a field that is
            # permanently blank on every AP in the estate.
            software_version=facts.software_version,
            description=f"AP group {access_point.group}" if access_point.group else "",
            is_access_point=True,
        ))
    return records
