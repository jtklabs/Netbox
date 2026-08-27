"""NTP servers.

The parser is deliberately tolerant: it keeps the device's own line verbatim so
that removal negates exactly what is configured, including options this tool
does not model (``prefer``, ``source Vlan10``, ``key 1``, a vrf).
"""

from __future__ import annotations

import argparse
from typing import List

from ..core import Desired, Entry, Feature, PlatformSupport, normalize, validate_address

# `include ^ntp server` rather than `section ntp`: we only ever want to see (and
# therefore only ever risk removing) server statements, never `ntp source`,
# `ntp authenticate`, `ntp access-group` and friends.
SHOW_COMMAND = "show running-config | include ^ntp server"

IOS_SAMPLE = """\
ntp server 10.10.10.1
ntp server 10.10.10.2 prefer
ntp server vrf MGMT 192.168.5.5 source GigabitEthernet0/0
"""

EOS_SAMPLE = """\
ntp server 10.10.10.1 iburst
ntp server vrf MGMT 192.168.5.5 prefer iburst
ntp server time.example.net
"""


def parse_ntp_servers(output: str) -> List[Entry]:
    """Pull server addresses out of `ntp server ...` lines.

    Shared by IOS and EOS -- both render the statement as
    ``ntp server [vrf NAME] <host> [options...]``. A platform whose syntax
    differs gets its own parser rather than an extra branch in here.
    """
    entries: List[Entry] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line.startswith("ntp server"):
            continue
        tokens = line.split()
        index = 2  # skip 'ntp' 'server'
        if len(tokens) > index and tokens[index] == "vrf":
            index += 2  # skip 'vrf' '<name>'
        if index >= len(tokens):
            continue  # malformed / truncated line; nothing safe to key on
        entries.append(Entry(key=tokens[index], line=line))
    return entries


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-s",
        "--servers",
        action="append",
        required=True,
        metavar="ADDR[,ADDR...]",
        help="desired NTP server(s); repeatable and/or comma separated",
    )
    parser.add_argument(
        "--vrf",
        help="configure the servers in this VRF (must match how they are "
        "already configured, or existing lines look like different servers)",
    )
    parser.add_argument(
        "--prefer",
        metavar="ADDR",
        help="mark this server (which must also be in --servers) as preferred",
    )
    parser.add_argument(
        "--source",
        metavar="INTERFACE",
        help="source interface for the server statements",
    )
    parser.add_argument(
        "--no-iburst",
        dest="iburst",
        action="store_false",
        default=True,
        help="omit iburst on platforms whose template uses it (Arista)",
    )


def build_desired(args: argparse.Namespace) -> Desired:
    servers: List[str] = []
    seen = set()
    for chunk in args.servers:
        for value in chunk.split(","):
            if not value.strip():
                continue
            key = normalize(validate_address(value))
            if key not in seen:
                seen.add(key)
                servers.append(key)
    if not servers:
        raise ValueError("--servers did not contain any addresses")

    prefer = normalize(validate_address(args.prefer)) if args.prefer else None
    if prefer and prefer not in seen:
        raise ValueError(f"--prefer {args.prefer} is not one of --servers")

    return Desired(
        keys=servers,
        variables={
            "vrf": validate_address(args.vrf) if args.vrf else None,
            "prefer": prefer,
            "source": validate_address(args.source) if args.source else None,
            "iburst": args.iburst,
        },
    )


FEATURE = Feature(
    name="ntp",
    help="converge the NTP servers on each device",
    platforms={
        "cisco_ios": PlatformSupport(SHOW_COMMAND, parse_ntp_servers, IOS_SAMPLE),
        "arista_eos": PlatformSupport(SHOW_COMMAND, parse_ntp_servers, EOS_SAMPLE),
    },
    add_arguments=add_arguments,
    build_desired=build_desired,
    # 10.10.10.1 is already in both samples, so the selftest also shows that a
    # server already configured produces no command.
    selftest_args=[
        "--servers",
        "10.10.10.1,10.99.99.1,10.99.99.2",
        "--prefer",
        "10.99.99.1",
    ],
)
