"""Syslog collectors, trap severity and source interface.

One feature covers three kinds of line, so each parsed entry carries its kind
and the key encodes the *value*: `trap:informational` rather than `trap`. A
device set to `notifications` therefore has a key the desired state does not,
which is exactly what makes the change show up.

The scalars never take part in removal. `no logging trap notifications` clears
the setting whatever argument it is given, so negating a stale one after
setting the new one would undo the change; setting the new value replaces the
old by itself.
"""

from __future__ import annotations

import argparse
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import MODE_REPLACE, Desired, Entry, Feature, PlatformSupport, normalize
from ..core import validate_address, validate_text, validate_word
from ..standards import host_and_port, of as standards_of

SHOW_COMMAND = "show running-config | include ^logging"

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

IOS_SAMPLE = """\
logging trap notifications
logging source-interface Loopback0
logging host 10.1.1.50
logging host 10.9.9.9 transport udp port 1514
logging buffered 32768
logging origin-id string OLD-NAME
"""

EOS_SAMPLE = """\
logging trap informational
logging source-interface Management1
logging host 10.1.1.50
logging host 10.9.9.9 1514
"""


def _destination_key(host: str, port: int) -> str:
    return f"host:{normalize(host)}:{port}"


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

        if tokens[1] == "trap" and len(tokens) >= 3:
            entries.append(Entry(key=f"trap:{tokens[2]}", line=line, data={"kind": "trap"}))
        elif tokens[1] == "origin-id" and len(tokens) >= 3:
            arguments = " ".join(tokens[2:])
            entries.append(
                Entry(key=f"origin:{arguments}", line=line, data={"kind": "origin"})
            )
        elif tokens[1] == "source-interface" and len(tokens) >= 3:
            entries.append(
                Entry(key=f"source:{tokens[2]}", line=line, data={"kind": "source"})
            )
        elif tokens[1] == "host" and len(tokens) >= 3:
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
                    key=_destination_key(host, port),
                    line=line,
                    data={"kind": "host", "host": host, "port": port},
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
        for entry in current:
            if entry.key in wanted or entry.data.get("kind") != "host":
                continue  # scalars are replaced by setting them, never negated
            to_remove.append(entry)
    return to_add, to_remove


def add_arguments(parser: argparse.ArgumentParser) -> None:
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
        key = _destination_key(host, port)
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
        source = validate_word(str(source), "interface")
        key = f"source:{source}"
        keys.append(key)
        entries[key] = {"kind": "source", "source": source}

    vrf = standards.value("syslog.vrf")

    if not keys:
        raise ValueError(
            "nothing to configure: pass --destination or set syslog.destinations "
            "in the standards file"
        )

    return Desired(
        keys=keys,
        variables={"entries": entries, "vrf": validate_word(str(vrf), "vrf") if vrf else None},
    )


FEATURE = Feature(
    name="syslog",
    help="converge the syslog collectors, trap severity and source interface",
    platforms={
        "cisco_ios": PlatformSupport(SHOW_COMMAND, parse_logging, IOS_SAMPLE),
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
    selftest_args=[],
)
