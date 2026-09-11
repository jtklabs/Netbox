"""Read-only baselines. Preserve raw evidence, compare stable fields only.

Unrecognized critical output is an error, never an empty healthy table. NTC
templates provide table parsing; identity/count cross-checks cover NAC and MAC.
"""

import difflib
import json
import re
from collections import Counter

from ntc_templates.parse import parse_output

MAC = r"[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}"
ERROR = re.compile(r"(?im)^\s*(?:%\s*(?:Invalid|Incomplete|Ambiguous|Unknown|Error|Authorization|Permission)|Error:|FAILED:)")


def understood(command, output):
    if not isinstance(output, str) or ERROR.search(output):
        raise ValueError(f"device rejected {command!r}")
    return output


def normalized_config(text, boot=False):
    lines = []
    for line in text.splitlines():
        if re.match(r"^(Building configuration|Current configuration|Using \d+|! Last configuration change|! NVRAM config|version \d|ntp clock-period )", line):
            continue
        if boot and re.match(r"^(?:no )?boot (?:system|manual)(?: |$)", line):
            continue
        if line.strip():
            lines.append(line.rstrip())
    return "\n".join(lines)


def software(text):
    rows = re.findall(r"(?m)^\s*\*?\s*(\d+)\s+\d+\s+(C\S+)\s+(\d+\.\d+\.\d+[a-z]?)\s+\S+\s+(INSTALL|BUNDLE)\s*$", text)
    if not rows:
        raise ValueError("show version: no complete per-member model/version/mode table")
    if len({r[0] for r in rows}) != len(rows):
        raise ValueError("show version: duplicate member IDs")
    return {number: {"model": model, "version": release, "mode": mode}
            for number, model, release, mode in rows}


def stack(text):
    rows = re.findall(r"(?m)^\s*\*?\s*(\d+)\s+(Active|Standby|Member)\s+(" + MAC + r")\s+\d+\s+\S+\s+(\S+)\s*$", text)
    if not rows:
        raise ValueError("show switch: no recognized members")
    return {number: {"mac": mac.lower(), "state": state} for number, _, mac, state in rows}


# command, template command, stable fields, recognizable empty-table evidence,
# whether collection is required. None fields retains all parsed fields.
TABLES = {
    "inventory": ("show inventory", "show inventory", ("name", "pid", "sn"), r"PID:", True),
    "interfaces": ("show interfaces status", "show interfaces status", ("port", "status", "vlan_id", "duplex", "speed"), r"Port\s+Name\s+Status", True),
    "ip_interfaces": ("show ip interface brief", "show ip interface brief", ("interface", "ip_address", "status", "proto"), r"Interface\s+IP-Address", True),
    "nac": ("show access-session", "show access-session", ("interface", "mac_address", "method", "domain", "status"), r"Interface\s+(?:MAC Address|Identifier)\s+Method|No sessions|Total sessions\s*[:=]\s*0", True),
    "mac": ("show mac address-table", "show mac-address-table", None, r"Mac Address Table|No Mac|Total Mac Addresses", True),
    "vlans": ("show vlan brief", "show vlan", None, r"VLAN\s+Name\s+Status", True),
    "port_channels": ("show etherchannel summary", "show etherchannel summary", None, r"Group\s+Port-channel|Number of channel-groups in use:\s*0", True),
    "spanning_tree": ("show spanning-tree", "show spanning-tree", None, r"Spanning tree|No spanning tree|Spanning-tree", True),
    "stp_roots": ("show spanning-tree root", "show spanning-tree root", ("vlan_id", "priority", "root_id", "root_cost", "root_port"), r"Root ID|Root Hello|No spanning tree", False),
    "arp": ("show ip arp", "show ip arp", ("ip_address", "mac_address", "interface"), r"Protocol\s+Address|No ARP", False),
    "hsrp": ("show standby brief", "show standby brief", None, r"Interface\s+Grp|P indicates|No standby", False),
    "vrrp": ("show vrrp brief", "show vrrp brief", ("interface", "group", "addr_family", "state", "master_ip_address", "virtual_ip_address"), r"Interface\s+Grp|Interface\s+Group|No VRRP", False),
    "redundancy": ("show redundancy", "show redundancy", ("hardware_mode", "operating_redundancy_mode", "communication_status", "active_software_state", "standby_software_state", "standby_status"), r"Redundant System Information|Hardware Mode", False),
    "routes": ("show ip route", "show ip route", ("vrf", "protocol", "type", "network", "prefix_length", "nexthop_ip", "nexthop_if"), r"Codes:|Gateway of last resort|IP routing not enabled", True),
    "vrfs": ("show vrf", "show vrf", None, r"Name\s+Default RD\s+Protocols|No VRF", True),
    "ipv6_interfaces": ("show ipv6 interface brief", "show ipv6 interface brief", None, r"\[(?:up|down|administratively down)/|IPv6 is not enabled", False),
    "ipv6_routes": ("show ipv6 route", "show ipv6 route", None, r"IPv6 Routing Table|IPv6 routing not enabled|No IPv6", False),
    "poe": ("show power inline", "show power inline", ("interface", "admin_status", "operational_status", "device", "class"), r"Interface\s+Admin\s+Oper|No inline power", False),
    "cdp": ("show cdp neighbors detail", "show cdp neighbors detail", ("neighbor_name", "mgmt_address", "local_interface", "neighbor_interface"), r"Device ID:|Total cdp entries|CDP is not enabled", False),
    "lldp": ("show lldp neighbors detail", "show lldp neighbors detail", ("local_interface", "chassis_id", "neighbor_port_id", "neighbor_name", "mgmt_address"), r"Local Intf:|Local Interface:|Total entries|LLDP is not enabled", False),
}

DIAGNOSTICS = (
    "show boot", "show install summary", "show switch stack-ports summary",
    "show environment all", "show interfaces counters errors",
    "show processes cpu sorted", "show processes memory sorted", "show ntp associations",
    "show clock detail", "show logging", "show interfaces trunk",
    "show ip protocols", "show ipv6 protocols", "show ipv6 neighbors",
    "show access-session details", "show aaa servers", "show radius statistics",
)


def table(command, template, fields, header, output):
    understood(command, output)
    if not re.search(header, output, re.I):
        raise ValueError(f"{command}: unrecognized table/header")
    rows = parse_output(platform="cisco_ios", command=template, data=output)
    if not rows and command in {"show inventory", "show interfaces status", "show ip interface brief", "show vlan brief"}:
        raise ValueError(f"{command}: parser returned no records")
    if command in {"show access-session", "show authentication sessions", "show mac address-table"}:
        addresses = re.findall(MAC, output)
        parsed_addresses = [row.get("mac_address", row.get("destination_address", "")) for row in rows]
        if Counter(a.lower() for a in addresses) != Counter(a.lower() for a in parsed_addresses):
            raise ValueError(f"{command}: parser did not account for every MAC address")
        count = re.search(r"(?:Total (?:sessions|Mac Addresses for this criterion)|Session count)\s*[:=]\s*(\d+)", output, re.I)
        if count and int(count[1]) != len(rows):
            raise ValueError(f"{command}: parsed count differs from device count")
    count_patterns = {
        "show inventory": r"(?m)^\s*NAME:",
        "show interfaces status": r"(?m)^\s*(?:Gi|Te|Twe|Fo|Hu|Eth|Fa|Po)[A-Za-z-]*\d\S*\s+",
        "show ip interface brief": r"(?m)^\S+\s+(?:unassigned|\d+\.\d+\.\d+\.\d+)\s+",
        "show vlan brief": r"(?m)^\d+\s+\S+\s+(?:active|suspend|act/unsup|shutdown)",
        "show etherchannel summary": r"(?m)^\s*\d+\s+Po\d+\(",
    }
    if command in count_patterns and len(re.findall(count_patterns[command], output)) != len(rows):
        raise ValueError(f"{command}: parser did not account for every table row")
    if fields:
        if rows and not all(set(fields) <= set(row) for row in rows):
            raise ValueError(f"{command}: parser is missing required fields")
        rows = [{key: row[key] for key in fields} for row in rows]
    return rows


def routing_checks(config):
    """Discover protocols and VRFs from config, including named EIGRP AFs.

    BGP all-summary plus explicit per-VRF summaries cover configured address
    families. Section headers are part of each peer's identity.
    """
    checks, unsupported = {}, []
    for match in re.finditer(r"(?m)^((?:ipv6 )?router (\S+)([^\n]*))\n((?:[ \t].*\n|!\n)*)", config + "\n"):
        line, protocol, rest, body = match.groups()
        if protocol == "bgp":
            checks["bgp"] = ("show bgp all summary", "bgp")
            for family, vrf in re.findall(r"address-family (ipv4|ipv6)(?: unicast| multicast)? vrf (\S+)", body):
                if not re.fullmatch(r"[\w.-]+", vrf):
                    unsupported.append(line + " VRF " + vrf)
                    continue
                command = f"show bgp {'vpnv4' if family == 'ipv4' else 'vpnv6'} unicast vrf {vrf} summary"
                checks[command] = (command, "bgp")
        elif protocol in {"ospf", "ospfv3"}:
            pid = rest.strip().split()[0]
            if not pid.isdigit():
                unsupported.append(line)
                continue
            family = "ipv6 ospf" if line.startswith("ipv6 ") else "ospfv3" if protocol == "ospfv3" else "ip ospf"
            checks[line] = (f"show {family} {pid} neighbor", "ospf")
        elif protocol == "eigrp":
            vrfs = re.findall(r"address-family\s+(ipv4|ipv6)\s+(?:unicast\s+)?(?:vrf\s+(\S+)\s+)?autonomous-system", body)
            if not vrfs:
                vrfs = [("ipv6" if line.startswith("ipv6 ") else "ipv4", "")]
            for family, vrf in vrfs:
                if vrf and not re.fullmatch(r"[\w.-]+", vrf):
                    unsupported.append(line)
                    continue
                cmd = f"show {'ip' if family == 'ipv4' else 'ipv6'} eigrp" + (f" vrf {vrf}" if vrf else "") + " neighbors"
                checks[cmd] = (cmd, "eigrp")
        elif protocol == "isis":
            checks["isis"] = ("show isis neighbors", "isis")
        else:
            unsupported.append(line)
    return checks, unsupported


def metrics(snapshot):
    tables = snapshot["tables"]
    nac = tables.get("nac", [])
    return {"counts": {name: len(rows) for name, rows in tables.items()},
            "routing_peer_counts": {name: len(rows) for name, rows in snapshot["routing"].items()},
            "nac_status_counts": dict(Counter(row["status"] for row in nac)),
            "nac_by_port": dict(Counter(row["interface"] for row in nac)),
            "mac_by_port": dict(Counter(port for row in tables.get("mac", []) for port in row["destination_port"]))}


def peers(output, kind):
    rows = []
    if kind == "bgp":
        scope, router, pending = "default", "", ""
        for line in output.splitlines():
            if line.startswith("For address family:"):
                scope = line.split(":", 1)[1].strip()
            if "BGP router identifier" in line:
                router = line.strip()
            # IPv6 neighbors may wrap onto a separate physical line.
            if re.fullmatch(r"[0-9a-fA-F:.]+", line.strip()) and ":" in line:
                pending = line.strip()
                continue
            candidate = (pending + " " + line.strip()) if pending else line.strip()
            if re.match(r"^[0-9a-fA-F:.]+\s+4\s+", candidate):
                parts = candidate.split()
                if len(parts) < 10 or not router:
                    raise ValueError("BGP: incomplete neighbor row or missing router context")
                state = " ".join(parts[9:])
                rows.append({"scope": scope, "router": router, "neighbor": parts[0], "as": parts[2],
                             "state": "Established" if state.isdigit() else state,
                             "prefixes": state if state.isdigit() else None})
                pending = ""
        if pending or not re.search(r"BGP router identifier|BGP not active|No BGP", output, re.I):
            raise ValueError("BGP: unrecognized summary")
    elif kind == "ospf":
        if not re.search(r"Neighbor ID|No neighbors|not enabled", output, re.I):
            raise ValueError("OSPF: unrecognized neighbor table")
        scope = "default"
        for line in output.splitlines():
            if re.search(r"OSPFv3.*address.family|OSPFv3.*IPv[46]|Neighbors for OSPFv3", line, re.I):
                scope = line.strip()
            if re.match(r"^\s*\d+\.\d+\.\d+\.\d+\s+", line):
                parts = line.split()
                if len(parts) < 6:
                    raise ValueError("OSPF: incomplete neighbor row")
                state = parts[2]
                # Some tables print FULL/ - with whitespace.
                if state.endswith("/") and len(parts) > 3:
                    state += parts[3]
                rows.append({"scope": scope, "neighbor": parts[0], "state": state, "interface": parts[-1]})
    elif kind == "eigrp":
        autonomous_system, pending = "", ""
        if not re.search(r"EIGRP.*[Nn]eighbors", output):
            raise ValueError("EIGRP: unrecognized neighbor table")
        for line in output.splitlines():
            header = re.search(r"EIGRP.*[Nn]eighbors for (?:AS\((\d+)\)|process (\d+))", line)
            if header:
                autonomous_system = header[1] or header[2]
            candidate = pending + " " + line.strip() if pending else line.strip()
            if re.match(r"^\d+\s+[0-9A-Fa-f:.]+(?:\s|$)", candidate):
                parts = candidate.split()
                if len(parts) < 9:
                    pending = candidate
                    continue
                if len(parts) != 9 or not autonomous_system or not parts[7].isdigit():
                    raise ValueError("EIGRP: incomplete neighbor or AS context")
                rows.append({"as": autonomous_system, "ip_address": parts[1], "interface": parts[2], "q_cnt": parts[7]})
                pending = ""
        if pending:
            raise ValueError("EIGRP: incomplete wrapped neighbor row")
    else:
        rows = table("show isis neighbors", "show isis neighbors", ("system_id", "interface", "state", "type"), r"System Id", output)
        pattern = r"(?mi)^\s*\S+\s+.*\b(?:Up|Down|Init)\b.*$"
        if len(re.findall(pattern, output)) != len(rows):
            raise ValueError(f"{kind}: parser did not account for every neighbor row")
    return rows


def collect_staging(read, progress):
    """Image staging needs hardware/stack identity, not an upgrade baseline."""
    snapshot = {"raw": {}, "errors": {}, "warnings": {}}
    for command, key, parser in (("show version", "software", software), ("show switch", "stack", stack)):
        progress(command)
        try:
            output = read(command)
            snapshot["raw"][command] = output
            snapshot[key] = parser(understood(command, output))
        except Exception as exc:
            snapshot["errors"][key] = str(exc)
    snapshot["metrics"] = {"counts": {"stack_members": len(snapshot.get("stack", {}))}}
    return snapshot


def collect(read, progress, commands=None):
    snapshot = {"raw": {}, "tables": {}, "errors": {}, "warnings": {}, "routing": {}}

    def get(command):
        progress(command)
        output = read(command)
        snapshot["raw"][command] = output
        return understood(command, output)

    for command, key, parser in (
        ("show version", "software", software), ("show switch", "stack", stack),
        ("show running-config", "config", normalized_config),
        ("show startup-config", "startup_config", normalized_config),
    ):
        try:
            value = parser(get(command))
            if key.endswith("config") and not re.search(r"(?m)^hostname \S+", value):
                raise ValueError(f"{command}: incomplete configuration")
            snapshot[key] = value
        except Exception as exc:
            snapshot["errors"][key] = str(exc)
    for key, (command, template, fields, header, required) in TABLES.items():
        if key in {"hsrp", "vrrp"} and re.search(r"(?m)^\s*" + ("standby" if key == "hsrp" else "vrrp") + r"\s+\d+", snapshot.get("config", "")):
            required = True
        if key.startswith("ipv6_") and re.search(r"(?m)^\s*(?:ipv6 address|ipv6 unicast-routing|ipv6 router|router ospfv3|address-family ipv6)", snapshot.get("config", "")):
            required = True
        try:
            try:
                output = get(command)
            except ValueError:
                if key != "nac":
                    raise
                command = template = "show authentication sessions"
                output = get(command)
            snapshot["tables"][key] = table(command, template, fields, header, output)
        except Exception as exc:
            snapshot["errors" if required else "warnings"][key] = str(exc)
    for vrf in snapshot["tables"].get("vrfs", []):
        name = vrf["name"]
        if not re.fullmatch(r"[\w.-]+", name):
            snapshot["errors"]["vrf_routes"] = "VRF name cannot be represented safely in a show command"
            continue
        for family in ("ipv4", "ipv6"):
            if family not in vrf["protocols"].lower():
                continue
            command = f"show {'ip' if family == 'ipv4' else 'ipv6'} route vrf {name}"
            spec = TABLES["routes" if family == "ipv4" else "ipv6_routes"]
            try:
                snapshot["tables"][command] = table(command, spec[1], spec[2], spec[3], get(command))
            except Exception as exc:
                snapshot["errors"][command] = str(exc)
    routing, unsupported = routing_checks(snapshot.get("config", ""))
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


def health(snapshot):
    """Extract resource measurements and identifiable alarms from raw evidence."""
    raw = snapshot["raw"]
    values = {"alarms": [], "interface_errors": {}}
    cpu = re.search(r"CPU utilization for five seconds:\s*(\d+)%.*?one minute:\s*(\d+)%.*?five minutes:\s*(\d+)%", raw.get("show processes cpu sorted", ""))
    if cpu:
        values["cpu_percent"] = {"five_seconds": int(cpu[1]), "one_minute": int(cpu[2]), "five_minutes": int(cpu[3])}
    memory = re.search(r"Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)\s+Free:\s*(\d+)", raw.get("show processes memory sorted", ""))
    if memory and int(memory[1]):
        values["memory_free_percent"] = round(100 * int(memory[3]) / int(memory[1]), 2)
    ntp = raw.get("show ntp associations", "")
    if not ERROR.search(ntp) and re.search(r"address\s+ref clock", ntp, re.I):
        values["ntp"] = []
        for line in ntp.splitlines():
            match = re.match(r"^\s*([*+#~ox -]*)([\d.:a-fA-F]+)\s+\S+\s+(\d+)\s+\S+\s+\d+\s+(\d+)\s+", line)
            if match:
                values["ntp"].append({"server": match[2], "selected": "*" in match[1],
                                      "reachable": int(match[4]) > 0, "stratum": int(match[3])})
    for line in raw.get("show environment all", "").splitlines():
        if re.search(r"\b(?:FAULTY|FAIL(?:ED|URE)?|CRITICAL|WARNING|NOT OK|BAD)\b", line, re.I):
            values["alarms"].append(line.strip())
    columns = []
    for line in raw.get("show interfaces counters errors", "").splitlines():
        parts = line.split()
        if parts and parts[0] == "Port":
            columns = parts[1:]
        elif parts and columns and re.match(r"^(Gi|Te|Twe|Fo|Hu|Fa|Po)\S+", parts[0]):
            if len(parts) != len(columns) + 1 or not all(v.isdigit() for v in parts[1:]):
                snapshot["warnings"]["interface_errors"] = "unrecognized error counter row"
                continue
            values["interface_errors"].setdefault(parts[0], {}).update(dict(zip(columns, map(int, parts[1:]))))
    return values


def compare(before, after):
    findings = []
    for key, error in after["errors"].items():
        findings.append({"check": key, "severity": "error", "message": error})
    for category in ("tables", "routing"):
        for key in sorted(set(before[category]) | set(after[category])):
            old, new = before[category].get(key), after[category].get(key)
            if old is None or new is None:
                findings.append({"check": key, "severity": "error", "message": "baseline or post-check unavailable"})
                continue
            a = Counter(json.dumps(row, sort_keys=True) for row in old)
            b = Counter(json.dumps(row, sort_keys=True) for row in new)
            if a != b:
                findings.append({"check": key, "severity": "error", "before_count": len(old), "after_count": len(new),
                                 "removed": [json.loads(row) for row in (a-b).elements()],
                                 "added": [json.loads(row) for row in (b-a).elements()]})
    if before.get("stack") != after.get("stack"):
        findings.append({"check": "stack", "severity": "error", "message": "member identity or readiness changed"})
    for key in ("config", "startup_config"):
        a = normalized_config(before.get(key, ""), boot=True)
        b = normalized_config(after.get(key, ""), boot=True)
        if a != b:
            findings.append({"check": key, "severity": "error", "diff": "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), fromfile="before", tofile="after", lineterm=""))})
    for key, warning in after["warnings"].items():
        findings.append({"check": key, "severity": "warning", "message": warning})
    health_before, health_after = before.get("health", {}), after.get("health", {})
    if "ntp" in health_before:
        old = sorted(health_before["ntp"], key=lambda row: row["server"])
        new = sorted(health_after.get("ntp", []), key=lambda row: row["server"])
        if old != new:
            findings.append({"check": "ntp", "severity": "error", "before": old, "after": new})
    for alarm in sorted(set(health_after.get("alarms", [])) - set(health_before.get("alarms", []))):
        findings.append({"check": "environment", "severity": "error", "message": alarm})
    if health_after.get("cpu_percent", {}).get("one_minute", 0) >= 90:
        findings.append({"check": "cpu", "severity": "error", "message": "one-minute CPU utilization is at least 90%"})
    if health_after.get("memory_free_percent", 100) < 10:
        findings.append({"check": "memory", "severity": "error", "message": "processor free memory is below 10%"})
    for interface, counters in health_after.get("interface_errors", {}).items():
        previous = health_before.get("interface_errors", {}).get(interface, {})
        active = {key: value for key, value in counters.items() if value > previous.get(key, 0)}
        if active:
            findings.append({"check": "interface_errors", "severity": "warning", "interface": interface,
                             "message": "error counters exceed baseline", "counters": active})
    return findings
