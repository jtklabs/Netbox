"""Read-only Arista EOS baselines over SSH. Preserve raw evidence, compare stable fields only.

The snapshot has the same shape as the IOS XE collector's, so the shared
comparison, convergence loop and report apply unchanged: ``software`` and
``stack`` identity, normalized running/startup configuration, parsed tables,
routing neighbors, raw diagnostics and health. Unrecognized critical output is
an error, never an empty healthy table. NTC templates parse the tabular
commands; MLAG, spanning tree, LLDP, VRRP and VRF output are parsed here
because no template covers them the way the comparison needs.
"""

import re
from collections import Counter

from ntc_templates.parse import parse_output

from .checks import ERROR, MAC, VOLATILE_FIELDS, understood

# Interfaces as EOS abbreviates them in counter and neighbor tables (never the "Port" header).
COUNTER_PORT = re.compile(r"^(?:Et|Po|Ma)\d\S*$")
PORT_ROW = r"(?m)^(?:Et|Ma|Po)\d\S*\s+\S"
STP_ROLES = r"(?:root|designated|alternate|backup|disabled|master)"
STP_STATES = r"(?:forwarding|blocking|listening|learning|discarding|disabled|broken)"
STP_ROW = re.compile(r"^(\S+)\s+(" + STP_ROLES + r")\s+(" + STP_STATES + r")\s+(\d+)\s+([\d.]+)\s+(\S+)\s*$", re.I)
OSPF_STATE = re.compile(r"^(?:FULL|2WAY|INIT|DOWN|EXSTART|EXCHANGE|LOADING|ATTEMPT)(?:/\S+)?$", re.I)
# `router` blocks that configure something other than a neighbor-bearing
# routing protocol; they are common on EOS and must not block an upgrade.
IGNORED_ROUTERS = {"general", "multicast", "bfd", "pim", "igmp", "msdp", "traffic-engineering", "l2-vpn",
                   "path-selection", "service-insertion", "adaptive-virtual-topology", "segment-security", "internet-exit"}


def normalized_config(text, boot=False):
    """EOS keeps its metadata in single-bang comments that legitimately differ
    between running-config, startup-config and reboots: the `! Command:` line,
    the `! device:` banner with the running release, the startup-config save
    timestamp and the `! boot system` note. Boot settings live in
    flash:boot-config, not the configuration, so `boot` changes nothing here.
    Operator `!!` comments are configuration and are kept."""
    kept = []
    for line in text.splitlines():
        if not line.strip() or re.match(r"^\s*!(?!!)", line):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept)


def software(text):
    model = re.search(r"(?m)^Arista\s+(\S+)\s*$", text)
    release = re.search(r"(?m)^Software image version:\s+(\S+)\s*$", text)
    architecture = re.search(r"(?m)^Architecture:\s+(\S+)\s*$", text)
    if not model or not release:
        raise ValueError("show version: no model and software image version")
    return {"1": {"model": model[1], "version": release[1], "mode": architecture[1] if architecture else "unknown"}}


def identity(text):
    """The unit that answered: system MAC and serial, so the reload returns the same box."""
    mac = re.search(r"(?m)^System MAC address:\s+(\S+)\s*$", text)
    serial = re.search(r"(?m)^Serial number:\s*(\S*)\s*$", text)
    if not mac:
        raise ValueError("show version: no system MAC address")
    return {"1": {"mac": mac[1].lower(), "serial": serial[1] if serial else "", "state": "standalone"}}


def boot(text):
    match = re.search(r"(?m)^Software image:\s+(\S+)\s*$", text)
    if not match:
        raise ValueError("show boot-config: no software image line")
    return {"image": re.sub(r"^flash:/?", "", match[1])}


def mlag(text):
    """One row of MLAG configuration, status and port counts; state is required."""
    if not re.search(r"(?m)^MLAG Configuration:", text):
        raise ValueError("show mlag: unrecognized output")
    row, prefix = {}, ""
    for line in text.splitlines():
        header = re.match(r"^MLAG (\w+):", line)
        if header:
            prefix = "ports_" if header[1] == "Ports" else ""
            continue
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9 -]*?)\s*:\s*(.*?)\s*$", line)
        if match:
            row[prefix + re.sub(r"[^a-z0-9]+", "_", match[1].lower())] = match[2]
    if not row.get("state"):
        raise ValueError("show mlag: no MLAG state")
    return [row]


def mlag_interfaces(text):
    if not text.strip():
        return []
    if not re.search(r"(?im)^\s*mlag\s+desc\s+state\s+local\s+remote\s+status", text):
        raise ValueError("show mlag interfaces: unrecognized table")
    rows = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)\s+(?:.*?\s+)?(active-full|active-partial|inactive|disabled|configured)\s+(\S+)\s+(\S+)\s+(\S+/\S+)\s*$", line, re.I)
        if match:
            rows.append({"mlag": match[1], "state": match[2].lower(), "local_interface": match[3],
                         "remote_interface": match[4], "status": match[5]})
    if len(re.findall(r"(?m)^\s*\d+\s+\S", text)) != len(rows):
        raise ValueError("show mlag interfaces: parser did not account for every row")
    return rows


def spanning_tree(text):
    """Per-interface role/state rows and the root identity of every instance."""
    if not text.strip() or re.search(r"(?i)spanning[- ]tree (?:is )?disabled|No spanning tree", text):
        return [], []
    if "Spanning tree enabled protocol" not in text:
        raise ValueError("show spanning-tree: unrecognized output")
    rows, roots, instance = [], [], ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "Spanning tree enabled protocol" in line and index:
            instance = lines[index - 1].strip()
        root = re.match(r"^\s*Root ID\s+Priority\s+(\d+)\s*$", line)
        if root:
            following = "\n".join(lines[index + 1:index + 3])
            address = re.search(r"Address\s+(\S+)", following)
            roots.append({"instance": instance, "root_priority": root[1], "root_address": address[1] if address else "",
                          "local_root": "This bridge is the root" in following})
        match = STP_ROW.match(line)
        if match:
            rows.append({"instance": instance, "interface": match[1], "role": match[2].lower(),
                         "state": match[3].lower(), "cost": match[4], "type": match[6]})
    if len(re.findall(r"(?mi)^\S+\s+\S+\s+" + STP_STATES + r"\s+\d+\s", text)) != len(rows):
        raise ValueError("show spanning-tree: parser did not account for every interface row")
    return rows, roots


def lldp(text):
    if not re.search(r"(?m)^Port\s+Neighbor Device ID\s+Neighbor Port ID\s+TTL", text):
        raise ValueError("show lldp neighbors: unrecognized table")
    rows = []
    for line in text.splitlines():
        match = re.match(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s*$", line)
        if match and match[1] != "Port":
            rows.append({"local_interface": match[1], "neighbor_name": match[2], "neighbor_port_id": match[3]})
    if len(re.findall(PORT_ROW, text)) != len(rows):
        raise ValueError("show lldp neighbors: parser did not account for every row")
    return rows


def vrrp(text):
    if not text.strip():
        return []
    rows = []
    for block in re.split(r"(?m)^(?=\S+ - Group \d+)", text):
        header = re.match(r"^(\S+) - Group (\d+)", block)
        if not header:
            if block.strip():
                raise ValueError("show vrrp: unrecognized output")
            continue
        state = re.search(r"(?m)^\s+State is (\S+)", block)
        address = re.search(r"(?m)^\s+Virtual IPv4 address is (\S+)", block)
        priority = re.search(r"(?m)^\s+Priority is (\d+)", block)
        if not state:
            raise ValueError("show vrrp: group without a state")
        rows.append({"interface": header[1], "group": header[2], "state": state[1],
                     "virtual_ip": address[1] if address else "", "priority": priority[1] if priority else ""})
    return rows


def vrfs(text):
    if not re.search(r"(?im)^\s*VRF\s+(?:RD\s+)?Protocols\s+State\s+Interfaces", text):
        raise ValueError("show vrf: unrecognized table")
    rows = []
    for line in text.splitlines():
        match = re.match(r"^\s+(\S+)\s+(?:(?:<not set>|\S+:\S+)\s+)?(ipv[46](?:,ipv[46])?)\s+", line)
        if match:
            rows.append({"name": match[1], "protocols": match[2]})
    return rows


# command, template command, stable fields, recognizable evidence, required.
TABLES = {
    "interfaces": ("show interfaces status", "show interfaces status", ("port", "status", "vlan_id", "duplex", "speed"), r"Port\s+Name\s+Status", True),
    "ip_interfaces": ("show ip interface brief", "show ip interface brief", ("interface", "ip_address", "status", "protocol"), r"Interface\s+IP Address", True),
    "mac": ("show mac address-table", "show mac address-table", ("vlan_id", "mac_address", "type", "destination_port"), r"Mac Address Table", True),
    "vlans": ("show vlan", "show vlan", None, r"VLAN\s+Name\s+Status", True),
    "port_channels": ("show port-channel summary", "show port-channel summary", None, r"Port-Channel\s+Protocol\s+Ports|Number of channels in use", True),
    "arp": ("show ip arp", "show ip arp", ("ip_address", "mac_address", "interface"), r"Address\s+Age", False),
    "routes": ("show ip route", "show ip route", ("vrf", "protocol", "network", "prefix_length", "next_hop", "interface"), r"Codes:|Gateway of last resort|IP routing not enabled", True),
}

COUNT_PATTERNS = {
    "show ip interface brief": r"(?m)^\S+\s+(?:\d+\.\d+\.\d+\.\d+/\d+|unassigned)\s+",
    "show vlan": r"(?m)^\d+\s+\S+\s+(?:active|suspended)\b",
    "show port-channel summary": r"(?m)^\s*Po\d+\(",
    "show ip route": r"(?m)\b\d+\.\d+\.\d+\.\d+/\d+\s+(?:\[|is directly)",
    "show ip arp": r"(?m)^\d+\.\d+\.\d+\.\d+\s+",
}

DIAGNOSTICS = (
    "show boot-config", "show reload cause", "show inventory", "show environment all",
    "show interfaces counters errors", "show processes top once", "show ntp status",
    "show ntp associations", "show clock", "show logging", "show interfaces trunk",
    "show ipv6 route", "show ipv6 neighbors", "dir flash:",
)


def table(command, template, fields, header, output):
    understood(command, output)
    if not re.search(header, output, re.I):
        raise ValueError(f"{command}: unrecognized table/header")
    rows = parse_output(platform="arista_eos", command=template, data=output)
    if command == "show interfaces status":
        raw_ports = Counter(re.findall(r"(?m)^\s*([A-Za-z][A-Za-z-]*\d\S*)\s+", output))
        parsed_ports = Counter(row.get("port", "") for row in rows)
        if raw_ports != parsed_ports:
            missing = ", ".join(list((raw_ports - parsed_ports).elements())[:10]) or "none"
            unexpected = ", ".join(list((parsed_ports - raw_ports).elements())[:10]) or "none"
            raise ValueError(f"{command}: parser did not account for every table row "
                             f"(raw={sum(raw_ports.values())}, parsed={len(rows)}; missing ports: {missing}; unexpected ports: {unexpected})")
    if command == "show mac address-table":
        addresses = re.findall(MAC, output)
        if Counter(a.lower() for a in addresses) != Counter(row.get("mac_address", "").lower() for row in rows):
            raise ValueError(f"{command}: parser did not account for every MAC address")
        totals = re.findall(r"Total Mac Addresses for this criterion:\s*(\d+)", output, re.I)
        if totals and sum(map(int, totals)) != len(rows):
            raise ValueError(f"{command}: parsed count differs from device count")
    if template in COUNT_PATTERNS and len(re.findall(COUNT_PATTERNS[template], output)) != len(rows):
        raise ValueError(f"{command}: parser did not account for every table row")
    if fields:
        if rows and not all(set(fields) <= set(row) for row in rows):
            raise ValueError(f"{command}: parser is missing required fields")
        rows = [{key: row[key] for key in fields} for row in rows]
    else:
        rows = [{key: value for key, value in row.items() if key not in VOLATILE_FIELDS} for row in rows]
    return rows


def routing_checks(config):
    """Neighbor checks for the routing protocols the configuration enables."""
    checks, unsupported = {}, []
    for match in re.finditer(r"(?m)^(router (\S+)([^\n]*))\n((?:[ \t].*\n)*)", config + "\n"):
        line, protocol, _, body = match.groups()
        if protocol == "bgp":
            checks["bgp"] = ("show ip bgp summary vrf all", "bgp")
            if re.search(r"(?m)^\s+address-family ipv6\b", body):
                checks["bgp_ipv6"] = ("show ipv6 bgp summary vrf all", "bgp")
        elif protocol == "ospf":
            checks["ospf"] = ("show ip ospf neighbor vrf all", "ospf")
        elif protocol == "ospfv3":
            checks["ospfv3"] = ("show ipv6 ospf neighbor", "ospf")
        elif protocol == "isis":
            checks["isis"] = ("show isis neighbors", "isis")
        elif protocol not in IGNORED_ROUTERS:
            unsupported.append(line)
    return checks, unsupported


def peers(output, kind):
    rows = []
    if kind == "bgp":
        scope, router = "", ""
        for line in output.splitlines():
            section = re.match(r"^BGP summary information for VRF (\S+)", line)
            if section:
                scope, router = section[1], ""
                continue
            if re.match(r"^Router identifier ", line):
                router = line.strip()
                continue
            match = re.search(r"(?<![\w.:])([0-9a-fA-F:.]+)\s+4\s+(\d+(?:\.\d+)?)\s+\d+\s+\d+\s+\d+\s+\d+\s+\S+\s+(\S+)(?:\s+(\d+)\s+(\d+))?\s*$", line)
            if match:
                if not scope or not router:
                    raise ValueError("BGP: neighbor row outside a VRF summary")
                rows.append({"scope": scope, "router": router, "neighbor": match[1], "as": match[2],
                             "state": "Established" if match[3] == "Estab" else match[3], "prefixes": match[4]})
            elif re.search(r"\s4\s+\d+(?:\.\d+)?\s+\d+\s+\d+\s+\d+\s+\d+(?:\s|$)", line):
                raise ValueError("BGP: incomplete neighbor row")
        if not re.search(r"BGP summary information for VRF", output):
            raise ValueError("BGP: unrecognized summary")
    elif kind == "ospf":
        if not output.strip():
            return []
        if not re.search(r"Neighbor ID", output):
            raise ValueError("OSPF: unrecognized neighbor table")
        for line in output.splitlines():
            parts = line.split()
            if not parts or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", parts[0]):
                continue
            state = next((i for i, part in enumerate(parts) if OSPF_STATE.match(part)), None)
            if state is None or state < 2 or len(parts) < state + 2:
                raise ValueError("OSPF: incomplete neighbor row")
            rows.append({"scope": " ".join(parts[1:state - 1]) or "default", "neighbor": parts[0],
                         "state": parts[state], "interface": parts[-1]})
    else:
        if not output.strip():
            return []
        if not re.search(r"System Id", output, re.I):
            raise ValueError("ISIS: unrecognized neighbor table")
        for line in output.splitlines():
            match = re.match(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(L[12]+)\s+(\S+)\s+(\S+)\s+(UP|DOWN|INIT)\s+\d+\s+(\S+)\s*$", line, re.I)
            if match:
                rows.append({"instance": match[1], "vrf": match[2], "system_id": match[3], "type": match[4],
                             "interface": match[5], "state": match[7].upper()})
        if len(re.findall(r"(?mi)^\s*\S+\s+\S+\s+\S+\s+L[12]+\s+\S+\s+\S+\s+(?:UP|DOWN|INIT)\s", output)) != len(rows):
            raise ValueError("ISIS: parser did not account for every neighbor row")
    return rows


def metrics(snapshot):
    tables = snapshot["tables"]
    state = (tables.get("mlag") or [{}])[0].get("state")
    return {"counts": {name: len(rows) for name, rows in tables.items()},
            "routing_peer_counts": {name: len(rows) for name, rows in snapshot["routing"].items()},
            "mac_by_port": dict(Counter(port for row in tables.get("mac", []) for port in row["destination_port"])),
            "mlag_state": state}


def health(snapshot):
    """Resource measurements and identifiable alarms from raw evidence.

    CPU is the busy share from one `show processes top once` sample (100 minus
    idle); EOS has no one-minute utilization counter. Memory comes from
    `show version`."""
    raw = snapshot["raw"]
    values = {"alarms": [], "interface_errors": {}}
    idle = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*id\b", raw.get("show processes top once", ""))
    if idle:
        values["cpu_percent"] = {"one_minute": round(100 - float(idle[1]), 1)}
    total = re.search(r"(?m)^Total memory:\s+(\d+)", raw.get("show version", ""))
    free = re.search(r"(?m)^Free memory:\s+(\d+)", raw.get("show version", ""))
    if total and free and int(total[1]):
        values["memory_free_percent"] = round(100 * int(free[1]) / int(total[1]), 2)
    ntp = raw.get("show ntp associations", "")
    if not ERROR.search(ntp) and re.search(r"remote\s+refid", ntp, re.I):
        values["ntp"] = []
        for line in ntp.splitlines():
            match = re.match(r"^([*+#~ox -])?([\w.:-]+)\s+\S+\s+(\d+)\s+\S\s+(?:\d+|-)\s+\d+\s+(\d+)\s+", line)
            if match:
                values["ntp"].append({"server": match[2], "selected": match[1] == "*",
                                      "reachable": int(match[4]) > 0, "stratum": int(match[3])})
    for line in raw.get("show environment all", "").splitlines():
        if re.search(r"\b(?:FAULTY|FAIL(?:ED|URE)?|CRITICAL|WARNING|NOT OK|BAD)\b", line, re.I):
            values["alarms"].append(line.strip())
    columns = []
    for line in raw.get("show interfaces counters errors", "").splitlines():
        parts = line.split()
        if parts and parts[0] == "Port":
            columns = parts[1:]
        elif parts and columns and COUNTER_PORT.match(parts[0]):
            if len(parts) != len(columns) + 1 or not all(v.isdigit() for v in parts[1:]):
                snapshot["warnings"]["interface_errors"] = "unrecognized error counter row"
                continue
            values["interface_errors"].setdefault(parts[0], {}).update(dict(zip(columns, map(int, parts[1:]))))
    return values


def collect_staging(read, progress):
    """Image staging needs the unit's identity, not an upgrade baseline."""
    snapshot = {"raw": {}, "errors": {}, "warnings": {}}
    progress("show version")
    try:
        output = read("show version")
        snapshot["raw"]["show version"] = output
        understood("show version", output)
        snapshot["software"], snapshot["stack"] = software(output), identity(output)
    except Exception as exc:
        snapshot["errors"]["software"] = str(exc)
    snapshot["metrics"] = {"counts": {"stack_members": len(snapshot.get("stack", {}))}}
    return snapshot


def collect(read, progress, commands=None):
    snapshot = {"raw": {}, "tables": {}, "errors": {}, "warnings": {}, "routing": {},
                "diagnostic_commands": list(DIAGNOSTICS)}

    def get(command):
        progress(command)
        output = read(command)
        snapshot["raw"][command] = output
        return understood(command, output)

    try:
        output = get("show version")
        snapshot["software"], snapshot["stack"] = software(output), identity(output)
    except Exception as exc:
        snapshot["errors"]["software"] = str(exc)
    for command, key, parser in (
        ("show boot-config", "boot", boot),
        ("show running-config", "config", normalized_config),
        ("show startup-config", "startup_config", normalized_config),
    ):
        try:
            value = parser(get(command))
            if key.endswith("config") and not re.search(r"(?m)^end\s*$", value):
                raise ValueError(f"{command}: incomplete configuration")
            snapshot[key] = value
        except Exception as exc:
            snapshot["errors"][key] = str(exc)
    for key, (command, template, fields, header, required) in TABLES.items():
        try:
            snapshot["tables"][key] = table(command, template, fields, header, get(command))
        except Exception as exc:
            snapshot["errors" if required else "warnings"][key] = str(exc)
    config = snapshot.get("config", "")
    parsers = [("mlag", "show mlag", mlag, True), ("vrfs", "show vrf", vrfs, True),
               ("lldp", "show lldp neighbors", lldp, False),
               ("vrrp", "show vrrp", vrrp, bool(re.search(r"(?m)^\s+vrrp \d+", config)))]
    for key, command, parser, required in parsers:
        try:
            snapshot["tables"][key] = parser(get(command))
        except Exception as exc:
            snapshot["errors" if required else "warnings"][key] = str(exc)
    try:
        snapshot["tables"]["spanning_tree"], snapshot["tables"]["stp_roots"] = spanning_tree(get("show spanning-tree"))
    except Exception as exc:
        snapshot["errors"]["spanning_tree"] = str(exc)
    if (snapshot["tables"].get("mlag") or [{}])[0].get("state") == "Active":
        try:
            snapshot["tables"]["mlag_interfaces"] = mlag_interfaces(get("show mlag interfaces"))
        except Exception as exc:
            snapshot["errors"]["mlag_interfaces"] = str(exc)
    for vrf in snapshot["tables"].get("vrfs", []):
        name = vrf["name"]
        if not re.fullmatch(r"[\w.-]+", name):
            snapshot["errors"]["vrf_routes"] = "VRF name cannot be represented safely in a show command"
            continue
        if "ipv4" not in vrf["protocols"]:
            continue
        for key, command in (("routes", f"show ip route vrf {name}"), ("arp", f"show ip arp vrf {name}")):
            spec = TABLES[key]
            try:
                snapshot["tables"][command] = table(command, spec[1], spec[2], spec[3], get(command))
            except Exception as exc:
                snapshot["errors" if spec[4] else "warnings"][command] = str(exc)
    routing, unsupported = routing_checks(config)
    if unsupported:
        snapshot["errors"]["routing_detection"] = "unsupported routing configuration: " + "; ".join(unsupported)
    # Re-run every pre-check even if a protocol disappears from the config.
    routing.update(commands or {})
    snapshot["routing_commands"] = routing
    for key, (command, kind) in routing.items():
        try:
            snapshot["routing"][key] = peers(get(command), kind)
        except Exception as exc:
            snapshot["errors"][key] = str(exc)
    for command in DIAGNOSTICS:
        try:
            get(command)
        except Exception as exc:
            snapshot["warnings"][command] = str(exc)
    snapshot["metrics"] = metrics(snapshot)
    snapshot["health"] = health(snapshot)
    return snapshot
