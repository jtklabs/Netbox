"""Scan one device and print everything it said, with no NetBox involved.

This is the tool for the question "what does this box actually report?" — asked
when commissioning a new platform, when a model or serial comes out wrong, or
before pointing the scanner at a fleet for the first time. It needs nothing but
a credentials file: no NetBox URL, no token, no tags, no prefixes.

It prints two things, and the distinction matters when something looks wrong:

    what the device said       the raw MIB values, near enough verbatim
    what that would become     how the modelling layer reads them

A model arriving wrong is nearly always the first of those being empty rather
than the second being confused, and putting them side by side is what makes
that obvious in one look.
"""

from __future__ import annotations

import json
import sys

from . import mibs, vendors
from .collect import Collector, DeviceFacts
from .model import ScanResult, build_scan_result
from .snmp import CredentialSession, SnmpAuthError, SnmpError, SnmpTimeoutError

__all__ = ('probe', 'facts_to_dict')

# Wide enough for a Cisco interface name, narrow enough for an 80-column term.
_NAME_WIDTH = 26


def probe(collector: Collector, address: str, as_json: bool = False,
          save_walk: str = "", out=sys.stdout) -> int:
    """Scan `address` and print what came back. Returns a process exit code."""
    try:
        facts = collector.collect(address)
    except SnmpTimeoutError as exc:
        print(f"{address}: no response — {exc}", file=sys.stderr)
        print("  Check the address, that this box can reach it, and that SNMP is "
              "enabled on the device.", file=sys.stderr)
        return 1
    except SnmpAuthError as exc:
        print(f"{address}: answered, but rejected every credential set — {exc}",
              file=sys.stderr)
        print("  The device is reachable. Add its SNMPv3 user, or add the right "
              "credential set to this poller.", file=sys.stderr)
        return 1
    except SnmpError as exc:
        print(f"{address}: {exc}", file=sys.stderr)
        return 1

    result = build_scan_result(facts)

    if save_walk:
        _save_walk(collector, facts, address, save_walk)

    if as_json:
        json.dump(facts_to_dict(facts, result), out, indent=2, default=str)
        out.write("\n")
    else:
        _print_report(facts, result, out)
    return 0


# --- human-readable report --------------------------------------------------


_ENTITY_CLASSES = {
    1: "other", 2: "unknown", 3: "chassis", 4: "backplane", 5: "container",
    6: "powerSupply", 7: "fan", 8: "sensor", 9: "module", 10: "port",
    11: "stack", 12: "cpu",
}


def _entity_class(value: int) -> str:
    """entPhysicalClass as its name — the numbers mean nothing at a glance."""
    return _ENTITY_CLASSES.get(value, str(value))


def _print_report(facts: DeviceFacts, result: ScanResult, out) -> None:
    w = out.write
    w("\n" + "=" * 72 + "\n")
    w(f"  {facts.host}\n")
    w("=" * 72 + "\n")
    w(f"Answered on credential set {facts.credential_name!r}\n")

    _section(w, "SYSTEM")
    _row(w, "Name", facts.sys_name)
    _row(w, "Location", facts.sys_location)
    _row(w, "Contact", facts.sys_contact)
    _row(w, "Uptime", _uptime(facts.sys_uptime))
    _row(w, "Object ID", _object_id(facts.sys_object_id))
    if facts.sys_descr:
        w("  Description\n")
        for line in facts.sys_descr.splitlines():
            w(f"      {line}\n")

    _section(w, "IDENTIFICATION")
    profile = facts.profile
    _row(w, "Vendor profile", profile.name if profile else "(none — unknown enterprise)")
    primary = result.primary
    _row(w, "Manufacturer", primary.manufacturer if primary else "")
    _row(w, "Platform", primary.platform if primary else "")
    _row(w, "Software", facts.software_version or "(not reported)")
    if facts.vendor_model:
        _row(w, "Model (vendor OID)", facts.vendor_model)
    if facts.vendor_serial:
        _row(w, "Serial (vendor OID)", facts.vendor_serial)

    # Shown whenever something is missing, because "no version" has two very
    # different causes and the report should say which: either no profile
    # matched this sysObjectID so nothing was ever asked, or the OIDs were
    # asked and the device had nothing at them. Only the second is a wrong OID.
    missing = [
        label for label, value in (
            ("software version", facts.software_version),
            ("model", facts.vendor_model),
            ("serial", facts.vendor_serial),
        ) if not value
    ]
    if missing and profile is None:
        w(f"\n  No vendor profile matches this sysObjectID, so no vendor OIDs\n")
        w(f"  were asked for at all — hence no {', '.join(missing)}.\n")
        w(f"  The enterprise arc is what selects the profile; send the walk\n")
        w(f"  (--save-walk) if this platform needs one adding.\n")
    elif missing and facts.vendor_scalars:
        w(f"\n  Nothing reported for: {', '.join(missing)}. What was asked:\n")
        for oid, value in facts.vendor_scalars.items():
            kind = ("version" if oid in (profile.version_oids or ()) else
                    "serial" if oid in (profile.serial_oids or ()) else "model")
            shown = "(no such object on this device)" if value is None else (
                repr(value) if value else "(empty string)")
            w(f"    {kind:<8} {oid:<34} {shown}\n")
        w("  An OID the device does not implement is the usual cause; the walk\n")
        w("  (--save-walk) is what says where this platform puts it instead.\n")

    chassis = facts.chassis_entities()
    _section(w, f"CHASSIS — ENTITY-MIB ({len(chassis)})")
    if chassis:
        w(f"  {'idx':>6}  {'model':<20} {'serial':<20} {'hw':<8} {'sw'}\n")
        for entity in chassis:
            w(f"  {entity.index:>6}  {entity.model or '—':<20} "
              f"{entity.serial or '—':<20} {entity.hardware_rev or '—':<8} "
              f"{entity.software_rev or '—'}\n")
    else:
        w("  none — this device does not populate entPhysicalTable.\n")
        w("  That is normal for firewalls and load balancers; the model and\n")
        w("  serial then come from the vendor OIDs above.\n")

    # When no model came back, show the whole entity table raw. The chassis
    # view above reads entPhysicalModelName (.13) and plenty of platforms leave
    # it empty while describing themselves perfectly well in entPhysicalDescr
    # (.2) or entPhysicalName (.7). Printing every row is what turns "no model"
    # into a specific answer about where this platform keeps it, without
    # anybody having to guess an OID.
    if not facts.vendor_model and not any(e.model for e in facts.entities):
        _section(w, f"ENTITY-MIB, EVERY ROW ({len(facts.entities)})")
        if not facts.entities:
            w("  The table is empty, so nothing here describes the hardware.\n")
            w("  If some other OID carries the model, send the walk\n")
            w("  (--save-walk) and it can be read from there.\n")
            w("\n  Some platforms publish no model over SNMP at all — Firepower\n")
            w("  Threat Defense is one, confirmed against real hardware. For\n")
            w("  those there is nothing to find and nothing to add: onboard the\n")
            w("  device and type the model at review. Everything else the scan\n")
            w("  found is kept, and rescans carry on without needing it again.\n")
        else:
            w("  No row carried a model name (.13). What the rows do say:\n\n")
            w(f"  {'idx':>6}  {'class':<10} {'name':<24} descr\n")
            for entity in facts.entities:
                w(f"  {entity.index:>6}  {_entity_class(entity.entity_class):<10} "
                  f"{(entity.name or '—')[:24]:<24} {(entity.descr or '—')[:60]}\n")
            w("\n  If the model is visible above but not in the model column,\n")
            w("  this platform publishes it somewhere this scanner is not yet\n")
            w("  reading. Send the walk (--save-walk) and it can be. If it is\n")
            w("  not visible anywhere, type the model at review instead —\n")
            w("  some platforms genuinely do not report one.\n")

    if facts.stack_members:
        _section(w, f"STACK — CISCO-STACKWISE-MIB ({len(facts.stack_members)})")
        w(f"  {'#':>3}  {'role':<10} {'state':<14} {'mac':<20} entity\n")
        for member in facts.stack_members:
            w(f"  {member.switch_number:>3}  "
              f"{mibs.CSW_ROLE_NAMES.get(member.role, member.role):<10} "
              f"{mibs.CSW_STATE_NAMES.get(member.state, member.state):<14} "
              f"{member.mac_address or '—':<20} {member.entity_index}\n")

    modules = facts.module_entities()
    _section(w, f"MODULES ({len(modules)})")
    if modules:
        w(f"  {'bay / name':<32} {'model':<20} serial\n")
        for entity in modules:
            w(f"  {(entity.name or '—')[:32]:<32} {entity.model or '—':<20} "
              f"{entity.serial or '—'}\n")
    else:
        w("  none reported\n")

    ips_by_index: dict[int, list[str]] = {}
    for entry in facts.ips:
        ips_by_index.setdefault(entry.if_index, []).append(entry.cidr())

    _section(w, f"INTERFACES ({len(facts.interfaces)})")
    if facts.interfaces:
        w(f"  {'name':<{_NAME_WIDTH}} {'netbox type':<18} {'adm':<4} {'op':<4} "
          f"{'speed':<10} {'mtu':>6}  mac\n")
        for index in sorted(facts.interfaces):
            iface = facts.interfaces[index]
            name = iface.display_name()
            netbox_type = mibs.netbox_interface_type(
                iface.if_type, iface.speed_mbps, name
            )
            w(f"  {name[:_NAME_WIDTH]:<{_NAME_WIDTH}} {netbox_type:<18} "
              f"{'up' if iface.admin_up else 'down':<4} "
              f"{'up' if iface.oper_up else 'down':<4} "
              f"{_speed(iface.speed_mbps):<10} "
              f"{iface.mtu if iface.mtu else '—':>6}  {iface.phys_address or '—'}\n")
            if iface.alias:
                w(f"  {'':<{_NAME_WIDTH}} “{iface.alias}”\n")
            for cidr in ips_by_index.get(index, []):
                w(f"  {'':<{_NAME_WIDTH}} {cidr}\n")
    else:
        w("  none reported\n")

    if facts.access_points:
        _section(w, f"ACCESS POINTS — reported by this controller "
                    f"({len(facts.access_points)})")
        w(f"  {'name':<24} {'model':<14} {'serial':<18} {'address':<16} status\n")
        for ap in facts.access_points:
            w(f"  {(ap.name or '—')[:24]:<24} {ap.model or '—':<14} "
              f"{ap.serial or '—':<18} {ap.ip_address or '—':<16} "
              f"{'up' if ap.is_up else ap.status}\n")

    _section(w, "WHAT THIS WOULD BECOME IN NETBOX")
    if not result.devices:
        w("  nothing — no chassis was identified, so no device would be created.\n")
    for device in result.devices:
        bits = [device.name]
        if device.model:
            bits.append(f"{device.manufacturer} {device.model}")
        else:
            bits.append("NO MODEL — this device would be skipped")
        if device.serial:
            bits.append(f"serial {device.serial}")
        if device.vc_position is not None:
            bits.append(f"member {device.vc_position}"
                        + (" (master)" if device.vc_is_master else ""))
        w(f"  device   {' | '.join(bits)}\n")
        if device.interfaces:
            w(f"           {len(device.interfaces)} interfaces, "
              f"{sum(len(i.ip_addresses) for i in device.interfaces)} addresses\n")
        for module in device.modules:
            w(f"  module   {module.bay_name}: {module.model} "
              f"serial {module.serial or '—'}\n")
    if result.is_stack:
        w(f"  chassis  virtual chassis {result.virtual_chassis_name!r} "
          f"with {len(result.devices)} members\n")
    for ap in result.access_points:
        w(f"  device   {ap.name} | {ap.manufacturer} {ap.model} "
          f"serial {ap.serial or '—'} (access point)\n")
    w("\n")


def _section(w, title: str) -> None:
    w(f"\n{title}\n{'-' * len(title)}\n")


def _row(w, label: str, value) -> None:
    w(f"  {label:<20} {value if value not in (None, '') else '—'}\n")


def _speed(mbps) -> str:
    if not mbps:
        return "—"
    if mbps >= 1000 and mbps % 1000 == 0:
        return f"{mbps // 1000} Gbps"
    return f"{mbps} Mbps"


def _uptime(ticks: str) -> str:
    """sysUpTime is hundredths of a second since the agent restarted."""
    try:
        total = int(ticks) // 100
    except (TypeError, ValueError):
        return ticks or ""
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"


def _object_id(oid: str) -> str:
    """Show the enterprise arc and who it belongs to — never the model."""
    if not oid:
        return ""
    enterprise = vendors.enterprise_number(oid)
    if enterprise is None:
        return oid
    name = mibs.ENTERPRISE_MANUFACTURERS.get(enterprise, "unrecognised vendor")
    return f"{oid}   (enterprise {enterprise} — {name})"


# --- machine-readable -------------------------------------------------------


def facts_to_dict(facts: DeviceFacts, result: ScanResult) -> dict:
    """Everything collected, as plain data. For piping into something else."""
    return {
        "host": facts.host,
        "credential": facts.credential_name,
        "collected_at": facts.collected_at.isoformat() if facts.collected_at else None,
        "system": {
            "name": facts.sys_name,
            "description": facts.sys_descr,
            "object_id": facts.sys_object_id,
            "enterprise": vendors.enterprise_number(facts.sys_object_id),
            "location": facts.sys_location,
            "contact": facts.sys_contact,
            "uptime_ticks": facts.sys_uptime,
        },
        "identification": {
            "vendor_profile": facts.profile.name if facts.profile else None,
            "manufacturer": result.primary.manufacturer if result.primary else "",
            "platform": result.primary.platform if result.primary else "",
            "software_version": facts.software_version,
            "vendor_model_oid": facts.vendor_model,
            "vendor_serial_oid": facts.vendor_serial,
        },
        "entities": [
            {
                "index": e.index, "class": mibs.ENT_CLASS_NAMES.get(e.entity_class, e.entity_class),
                "name": e.name, "descr": e.descr, "model": e.model, "serial": e.serial,
                "manufacturer": e.mfg_name, "hardware_rev": e.hardware_rev,
                "firmware_rev": e.firmware_rev, "software_rev": e.software_rev,
                "contained_in": e.contained_in,
            }
            for e in facts.entities
        ],
        "stack_members": [
            {
                "switch_number": m.switch_number,
                "role": mibs.CSW_ROLE_NAMES.get(m.role, m.role),
                "state": mibs.CSW_STATE_NAMES.get(m.state, m.state),
                "mac": m.mac_address, "entity_index": m.entity_index,
            }
            for m in facts.stack_members
        ],
        "interfaces": [
            {
                "index": i, "name": iface.display_name(), "descr": iface.descr,
                "alias": iface.alias, "if_type": iface.if_type,
                "netbox_type": mibs.netbox_interface_type(
                    iface.if_type, iface.speed_mbps, iface.display_name()
                ),
                "speed_mbps": iface.speed_mbps, "mtu": iface.mtu,
                "mac": iface.phys_address,
                "admin_up": iface.admin_up, "oper_up": iface.oper_up,
            }
            for i, iface in sorted(facts.interfaces.items())
        ],
        "ip_addresses": [
            {"address": e.address, "prefix_length": e.prefix_length, "if_index": e.if_index}
            for e in facts.ips
        ],
        "access_points": [
            {"name": ap.name, "model": ap.model, "serial": ap.serial,
             "mac": ap.mac_address, "address": ap.ip_address, "group": ap.group,
             "up": ap.is_up}
            for ap in facts.access_points
        ],
        "would_create": {
            "devices": [
                {
                    "name": d.name, "manufacturer": d.manufacturer, "model": d.model,
                    "serial": d.serial, "platform": d.platform,
                    "software_version": d.software_version,
                    "vc_position": d.vc_position, "vc_master": d.vc_is_master,
                    "interfaces": len(d.interfaces), "modules": len(d.modules),
                }
                for d in result.devices
            ],
            "virtual_chassis": result.virtual_chassis_name or None,
            "access_points": len(result.access_points),
        },
    }


# --- fixture capture --------------------------------------------------------


def _save_walk(collector: Collector, facts: DeviceFacts, address: str, path: str) -> None:
    """Write a full walk in the recorded-walk format the test fixtures use.

    Captured with the credential set that actually worked, so it needs no
    second round of guessing. The file drops straight into tests/fixtures/ and
    replaces a synthetic device with a real one.
    """
    credential = next(
        (c for c in collector.credentials if c.name == facts.credential_name),
        collector.credentials[0],
    )
    try:
        with CredentialSession(credential, timeout=collector.timeout,
                               retries=collector.retries,
                               use_bulk=collector.use_bulk) as session:
            raw = session.walk_raw(address, "1.3.6.1")
    except SnmpError as exc:
        print(f"  could not capture a walk: {exc}", file=sys.stderr)
        return

    header = (
        f"# {address} — captured by snmp_inventory.py --probe --save-walk\n"
        f"# Format is `snmpwalk -On -Oe` output, which is what the emulator and\n"
        f"# the parsing tests read. Drop it in tests/fixtures/ as-is.\n"
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header)
        handle.write(raw)
    print(f"  walk saved to {path} ({raw.count(chr(10))} varbinds)", file=sys.stderr)
