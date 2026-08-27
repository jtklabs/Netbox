"""SNMP maximum packet size.

`snmp-server packetsize` caps the largest SNMP payload IOS will emit; lowering
it to 1300 keeps big GETBULK replies inside the path MTU instead of relying on
fragmentation that firewalls and tunnels tend to drop.

Arista is declared not-applicable rather than unsupported: EOS has no
equivalent knob, so an EOS device in a mixed run is reported as skipped instead
of failing the run or being sent IOS syntax.
"""

from __future__ import annotations

import argparse
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from ..core import Desired, Entry, Feature, PlatformSupport

SHOW_COMMAND = "show running-config | include ^snmp-server packetsize"

#: IOS accepts 484-17940. The platform default is 1500 and is not written to
#: the running config, so a default device parses as having nothing set.
MIN_SIZE = 484
MAX_SIZE = 17940
DEFAULT_SIZE = 1300

IOS_SAMPLE = "snmp-server packetsize 1500\n"

EOS_NOT_APPLICABLE = (
    "EOS has no `snmp-server packetsize` equivalent (verify against your EOS "
    "release before assuming a device is compliant)"
)


def parse_packetsize(output: str) -> List[Entry]:
    entries: List[Entry] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line.startswith("snmp-server packetsize"):
            continue
        tokens = line.split()
        if len(tokens) >= 3:
            entries.append(Entry(key=tokens[2], line=line))
    return entries


def plan_packetsize(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    """A scalar setting, not a set: writing the new value replaces the old one,
    so there is never anything to negate and --replace behaves like --add."""
    wanted = desired[0]
    if any(entry.key == wanted for entry in current):
        return [], []
    return [wanted], []


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--size",
        type=int,
        default=DEFAULT_SIZE,
        help="maximum SNMP packet size in bytes",
    )


def build_desired(args: argparse.Namespace) -> Desired:
    if not MIN_SIZE <= args.size <= MAX_SIZE:
        raise ValueError(f"--size must be between {MIN_SIZE} and {MAX_SIZE}")
    return Desired(keys=[str(args.size)], variables={})


FEATURE = Feature(
    name="snmp-packetsize",
    help=f"set the SNMP maximum packet size (default {DEFAULT_SIZE})",
    platforms={"cisco_ios": PlatformSupport(SHOW_COMMAND, parse_packetsize, IOS_SAMPLE)},
    not_applicable={"arista_eos": EOS_NOT_APPLICABLE},
    add_arguments=add_arguments,
    build_desired=build_desired,
    plan=plan_packetsize,
    selftest_args=[],
)
