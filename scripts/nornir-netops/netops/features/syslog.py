"""Syslog collectors, trap severity and source interface.

One feature covers three kinds of line, so each parsed entry carries its kind
and the key encodes the *value*: `trap:informational` rather than `trap`. A
device set to `notifications` therefore has a key the desired state does not,
which is exactly what makes the change show up.

The scalars never take part in removal. `no logging trap notifications` clears
the setting whatever argument it is given, so negating a stale one after
setting the new one would undo the change; setting the new value replaces the
old by itself.

**This reads `show running-config all`.** A setting sitting at its platform
default is not written to the running config -- `logging trap informational` is
IOS's default and simply does not appear -- so a plain `show running-config`
cannot tell "not configured" from "configured to the default". That would make
the severity look missing on every run: set it, read back, still not there,
report it unverified, and do the same again tomorrow. `all` renders the
defaults explicitly, and the `| include` keeps the transfer small even though
the device generates more.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import MODE_REPLACE, Desired, Entry, Feature, PlatformSupport, normalize
from ..core import validate_address, validate_text, validate_word
from ..netbox import source_for
from ..standards import host_and_port, of as standards_of
from .. import f5_syslog, syslog_nxos
from ..ntp_discovery import interface_name
from .waf import add_arguments as f5_arguments, connection_settings, selected_policy, device_policy

SHOW_COMMAND = "show running-config all | include ^logging"

DEFAULT_PORT = 514

#: `logging origin-id` takes one of these, or `string <text>`.
ORIGIN_KEYWORDS = ("hostname", "ip", "ipv6")

#: IOS and EOS both accept these; the file states one of them.
SEVERITIES = (
    "emergencies",
    "alerts",
    "critical",
    "errors",
    "warnings",
    "notifications",
    "informational",
    "debugging",
)

# As `show running-config all` renders it: the defaults are present too, which
# is the whole point of asking for them.
IOS_SAMPLE = """\
logging exception 4096
logging count
logging trap notifications
logging facility local7
logging buffered 32768 debugging
logging console debugging
logging source-interface Loopback0
logging host 10.1.1.50 transport udp port 514
logging host 10.9.9.9 transport udp port 1514
logging origin-id string OLD-NAME
"""

EOS_SAMPLE = """\
logging buffered 16384 informational
logging console errors
logging trap informational
logging source-interface Management1
logging host 10.1.1.50
logging host 10.9.9.9 1514
"""


def _destination_key(host: str, port: int, vrf=None) -> str:
    return f"host:{normalize(host)}:{port}" + (f":vrf:{vrf}" if vrf else "")


def _source_key(source, vrf=None):
    return f"source:{source}" + (f":vrf:{vrf}" if vrf else "")


def parse_logging(output: str) -> List[Entry]:
    """Pick out the three kinds of line we manage and ignore the rest.

    `logging buffered`, `logging console` and friends are deliberately not
    parsed: an entry that is never produced can never be removed by --replace.
    """
    entries: List[Entry] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line.startswith("logging "):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        vrf = None
        if tokens[1] in ("vrf", "host", "source-interface", "local-interface") and "vrf" in tokens:
            index = tokens.index("vrf")
            if index + 1 == len(tokens):
                raise ValueError(f"invalid syslog VRF configuration: {line}")
            vrf = tokens[index + 1]
            vrf = None if vrf == 'default' else vrf
            tokens = tokens[:index] + tokens[index + 2:]
            if len(tokens) < 3:
                raise ValueError(f"invalid syslog VRF configuration: {line}")

        if tokens[1] == "trap" and len(tokens) >= 3:
            entries.append(Entry(key=f"trap:{tokens[2]}", line=line, data={"kind": "trap"}))
        elif tokens[1] == "origin-id" and len(tokens) >= 3:
            arguments = " ".join(tokens[2:])
            entries.append(
                Entry(key=f"origin:{arguments}", line=line, data={"kind": "origin"})
            )
        elif tokens[1] in ("source-interface", "local-interface") and len(tokens) >= 3:
            source = interface_name(''.join(tokens[2:]))
            entries.append(
                Entry(key=_source_key(source, vrf), line=line,
                      data={"kind": "source", "source": source, "vrf": vrf})
            )
        elif tokens[1] == "host" and len(tokens) >= 3:
            if tokens[2] == "ipv6":
                tokens.pop(2)
                if len(tokens) < 3:
                    raise ValueError(f"invalid syslog host configuration: {line}")
            host = tokens[2]
            port = DEFAULT_PORT
            rest = tokens[3:]
            if "port" in rest:  # IOS: transport udp port 1514
                index = rest.index("port")
                if index + 1 < len(rest) and rest[index + 1].isdigit():
                    port = int(rest[index + 1])
            elif rest and rest[0].isdigit():  # EOS: logging host <ip> <port>
                port = int(rest[0])
            entries.append(
                Entry(
                    key=_destination_key(host, port, vrf),
                    line=line,
                    data={"kind": "host", "host": host, "port": port, "vrf": vrf},
                )
            )
    return entries


def plan_syslog(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    context = context or {}
    if context.get('platform') == 'cisco_nxos':
        return syslog_nxos.plan(current, desired, mode, context)
    ignores = context.get("ignores") or ()
    entries: Mapping[str, Any] = (context.get("variables") or {}).get("entries", {})
    # A platform that cannot express a kind of line simply does not get it.
    # Leaving it in the desired set would make the device look out of
    # compliance on every run, forever, over something it cannot do.
    desired = [key for key in desired if entries.get(key, {}).get("kind") not in ignores]

    configured = {entry.key for entry in current}
    to_add = [key for key in desired if key not in configured]

    to_remove: List[Entry] = []
    if mode == MODE_REPLACE:
        wanted = set(desired)
        retained = set()
        for entry in current:
            if entry.data.get("kind") != "host":
                continue  # scalars are replaced by setting them, never negated
            if entry.key not in wanted or entry.key in retained:
                to_remove.append(entry)
            else:
                retained.add(entry.key)
    return to_add, to_remove


def execution_policy(host, mode, variables):
    return device_policy(host, mode, variables["netbox_policy"], variables["logging_policy"])


def audit_fields(current, desired, context):
    missing, extra = plan_syslog(current, desired, MODE_REPLACE, context)
    variables = context["variables"]
    source = next((e["source"] for e in variables["entries"].values() if e["kind"] == "source"), None)
    return {
        "syslog_compliant": not missing and not extra,
        "syslog_audit": {"missing": missing, "extra": [e.shown for e in extra],
                         "source_interface": source},
    }


def reverse(commands, current, removed, context):
    from ..rollback import default_reversal
    if context.get('platform') == 'cisco_nxos':
        return syslog_nxos.reverse(commands, current, removed, context)
    return default_reversal(commands, current, removed, context.get('secrets', ()))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    f5_arguments(parser)
    parser.add_argument(
        "--syslog-source-tag", default=os.environ.get("NETBOX_SYSLOG_SOURCE_TAG"),
        metavar="TAG", help="NetBox interface tag for the syslog source "
        "[$NETBOX_SYSLOG_SOURCE_TAG; default: service-source]",
    )
    parser.add_argument(
        "-d",
        "--destination",
        action="append",
        metavar="ADDR[:PORT]",
        help="collector(s); repeatable and/or comma separated. Defaults to "
        "syslog.destinations in the standards file.",
    )
    parser.add_argument(
        "--severity",
        choices=SEVERITIES,
        help="trap severity; defaults to syslog.severity in the standards file",
    )
    parser.add_argument(
        "--source",
        metavar="INTERFACE",
        help="source interface; defaults to syslog.source in the standards file",
    )
    parser.add_argument(
        "--origin-id",
        metavar="HOSTNAME|IP|IPV6|TEXT",
        help="identifier prepended to messages sent to a collector. One of the "
        "keywords, or any other text to send it as a string. Defaults to "
        "syslog.origin_id in the standards file. Cisco only.",
    )


def build_desired(args: argparse.Namespace) -> Desired:
    standards = standards_of(args)
    vrf = standards.value("syslog.vrf")
    vrf = validate_word(str(vrf), "vrf") if vrf else None

    raw: List[Any] = []
    if args.destination:
        for chunk in args.destination:
            raw.extend(value for value in chunk.split(",") if value.strip())
    else:
        raw = standards.entries("syslog.destinations")

    keys: List[str] = []
    entries: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, str) and item.count(":") == 1:  # addr:port shorthand
            host, _, port = item.partition(":")
            item = {"host": host, "port": int(port)}
        record = host_and_port(item, DEFAULT_PORT)
        host = validate_address(str(record["host"]))
        port = int(record["port"])
        key = _destination_key(host, port, vrf)
        if key not in entries:
            keys.append(key)
            entries[key] = {"kind": "host", "host": normalize(host), "port": port}

    severity = args.severity or standards.value("syslog.severity")
    if severity:
        if severity not in SEVERITIES:
            raise ValueError(
                f"unknown syslog severity {severity!r} (expected one of: "
                f"{', '.join(SEVERITIES)})"
            )
        key = f"trap:{severity}"
        keys.append(key)
        entries[key] = {"kind": "trap", "severity": severity}

    origin = args.origin_id or standards.value("syslog.origin_id")
    if origin:
        text = validate_text(str(origin), "origin-id")
        arguments = text if text in ORIGIN_KEYWORDS else f"string {text}"
        key = f"origin:{arguments}"
        keys.append(key)
        entries[key] = {"kind": "origin", "arguments": arguments}

    source = args.source or standards.value("syslog.source")
    if source:
        source = interface_name(str(source))
        key = _source_key(source, vrf)
        keys.append(key)
        entries[key] = {"kind": "source", "source": source}

    if not keys:
        raise ValueError(
            "nothing to configure: pass --destination or set syslog.destinations "
            "in the standards file"
        )

    return Desired(
        keys=keys,
        variables={"entries": entries, "vrf": vrf,
                   "f5": connection_settings(args),
                   "logging_policy": selected_policy(args),
                   "netbox_policy": bool(getattr(args, "netbox", False))},
    )


def per_device(keys, variables, host):
    """Swap in this device's source interface, if the inventory knows one.

    An authoritative "no interface" really does mean no `logging
    source-interface` on that device, so the entry is dropped rather than
    falling back to the fleet-wide value.
    """
    source, authoritative = source_for(host, "syslog")
    vrf = variables.get("vrf")
    selected_vrf = (getattr(host, "data", {}) or {}).get("syslog_vrf")
    if selected_vrf:
        vrf = None if selected_vrf == "default" else validate_word(str(selected_vrf), "syslog VRF")
    if not authoritative and vrf == variables.get("vrf"):
        return keys, variables
    if not authoritative:
        source = next((entry["source"] for entry in variables["entries"].values()
                       if entry["kind"] == "source"), None)
    source = interface_name(str(source)) if source else None

    entries, rebuilt = {}, []
    for key in keys:
        entry = variables["entries"][key]
        if entry["kind"] == "source":
            continue
        if entry["kind"] == "host":
            key = _destination_key(entry["host"], entry["port"], vrf)
        entries[key] = entry
        rebuilt.append(key)
    if source:
        key = _source_key(source, vrf)
        rebuilt.append(key)
        entries[key] = {"kind": "source", "source": source}
    return rebuilt, {**variables, "entries": entries, "vrf": vrf}


FEATURE = Feature(
    name="syslog",
    help="converge the syslog collectors, trap severity and source interface",
    platforms={
        "cisco_ios": PlatformSupport(SHOW_COMMAND, parse_logging, IOS_SAMPLE),
        "cisco_nxos": PlatformSupport(syslog_nxos.SHOW_COMMAND, syslog_nxos.parse, syslog_nxos.SAMPLE,
                                      ignores=("origin",), extra_commands=(syslog_nxos.STATUS_COMMAND,)),
        # EOS has no `logging origin-id`; the nearest thing is `logging format
        # hostname ...`, which is a different setting rather than a spelling of
        # this one. Declared here so an EOS device is not reported out of
        # compliance forever over a line it cannot have.
        "arista_eos": PlatformSupport(
            SHOW_COMMAND, parse_logging, EOS_SAMPLE, ignores=("origin",)
        ),
    },
    add_arguments=add_arguments,
    build_desired=build_desired,
    plan=plan_syslog,
    per_device=per_device,
    selftest_args=[],
    platform_runs={"f5_tmsh": f5_syslog.run},
    execution_policy=execution_policy,
    audit_fields=audit_fields,
    reverse=reverse,
    verify_with_plan=True,
)
