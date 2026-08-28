"""Read-only checks: is the thing we configured actually working?

Converging `ntp server 10.50.0.10` onto a device says nothing about whether the
device can reach it, whether the association ever came up, or whether the clock
is synchronised. That is what these answer, and they change nothing -- they run
show commands and report.

A check is deliberately not a Feature. It has no template, no desired/current
diff and no notion of applying; it reads operational state and judges it.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .core import UnsupportedPlatform, normalize
from .standards import host_and_port, of as standards_of

OK, WARN, FAIL = "ok", "warn", "fail"

#: An NTP association's selection state, as the leading character of its line.
#: `~` only means "configured" and carries no health information.
SELECTION = {
    "*": "sys.peer",
    "#": "selected",
    "+": "candidate",
    "-": "outlyer",
    "x": "falseticker",
}

#: reach is an 8-bit shift register of the last eight polls, printed in octal.
#: 377 is all eight; 0 is nothing has ever come back.
REACH_PERFECT = 0o377


@dataclass(frozen=True)
class CheckSupport:
    commands: Tuple[str, ...]
    parse: Callable[[str], Dict[str, Any]]
    sample: str = ""


@dataclass
class Verdict:
    status: str
    summary: str
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Check:
    name: str
    help: str
    platforms: Dict[str, CheckSupport]
    evaluate: Callable[[Mapping[str, Any], Sequence[str], Mapping[str, Any]], Verdict]
    add_arguments: Callable[[argparse.ArgumentParser], None]
    expected: Callable[[argparse.Namespace], List[str]]

    def support_for(self, platform: str) -> CheckSupport:
        try:
            return self.platforms[platform]
        except KeyError:
            raise UnsupportedPlatform(
                f"platform {platform!r} has no '{self.name}' check "
                f"(supported: {', '.join(sorted(self.platforms))})"
            ) from None


# --------------------------------------------------------------------------- #
# ntp
# --------------------------------------------------------------------------- #

NTP_COMMANDS = ("show ntp status", "show ntp associations")

# Flags may run straight into the address: `*~10.50.0.10`.
_ASSOCIATION = re.compile(r"^\s*(?P<flags>[*#+\-x~]*)(?P<address>[0-9A-Za-z][0-9A-Za-z.:\-]*)\s+(?P<rest>.+)$")
_STRATUM = re.compile(r"stratum\s+(\d+)", re.IGNORECASE)
_IOS_REFERENCE = re.compile(r"reference is\s+(\S+)", re.IGNORECASE)
_EOS_REFERENCE = re.compile(r"synchroni[sz]ed to (?:NTP server )?(\S+)", re.IGNORECASE)

IOS_SAMPLE = """\
Clock is synchronized, stratum 2, reference is 10.50.0.10
nominal freq is 250.0000 Hz, actual freq is 249.9999 Hz, precision is 2**18
system poll interval is 64, last update was 30 sec ago.
  address         ref clock       st   when   poll reach  delay  offset   disp
*~10.50.0.10      .GPS.            1     32     64   377  1.234   0.123  0.456
+~10.50.0.11      10.1.1.1         2     44     64   377  2.345  -0.234  0.567
 ~10.9.9.9        .INIT.          16      -   1024     0  0.000   0.000 15937.
 * sys.peer, # selected, + candidate, - outlyer, x falseticker, ~ configured
"""

EOS_SAMPLE = """\
synchronised to NTP server 10.50.0.10 at stratum 3
   time correct to within 51 ms
     remote           refid      st t when poll reach   delay   offset  jitter
==============================================================================
*10.50.0.10      .GPS.            2 u   19   64  377    1.234    0.123   0.456
 10.50.0.11      .INIT.          16 u    -  128    0    0.000    0.000   0.000
"""


def _number(text: str) -> Optional[float]:
    try:
        return float(text.rstrip("."))
    except (TypeError, ValueError):
        return None


def parse_ntp_status(output: str) -> Dict[str, Any]:
    """Read `show ntp status` and `show ntp associations` together.

    The two platforms print the association table with a different number of
    columns -- IOS has no `t` column -- but in both the address is first, the
    stratum is the second field after it, and the last four are reach, delay,
    offset and dispersion/jitter. Counting from both ends handles both.
    """
    synchronized: Optional[bool] = None
    stratum: Optional[int] = None
    reference: Optional[str] = None
    associations: List[Dict[str, Any]] = []

    for raw in output.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        lowered = stripped.lower()
        if "unsynchroni" in lowered:
            synchronized = False
        elif "synchroni" in lowered and synchronized is None:
            synchronized = True
            match = _EOS_REFERENCE.search(stripped)
            if match:
                reference = match.group(1)
        if synchronized is not None and stratum is None:
            match = _STRATUM.search(stripped)
            if match:
                stratum = int(match.group(1))
        if reference is None:
            match = _IOS_REFERENCE.search(stripped)
            if match and match.group(1).lower() not in ("clock", "no"):
                reference = match.group(1)

        # The legend and the header are not associations.
        if lowered.startswith(("address", "remote", "====")) or "sys.peer," in lowered:
            continue
        match = _ASSOCIATION.match(line)
        if not match:
            continue
        fields = match.group("rest").split()
        if len(fields) < 7:
            continue  # not a table row
        try:
            row_stratum = int(fields[1])
            reach = int(fields[-4], 8)  # octal, as ntp prints it
        except ValueError:
            continue
        flags = match.group("flags")
        associations.append(
            {
                "address": normalize(match.group("address")),
                "state": next(
                    (SELECTION[f] for f in flags if f in SELECTION), "rejected"
                ),
                "stratum": row_stratum,
                "reach": reach,
                "reach_octal": fields[-4],
                "offset": _number(fields[-2]),
                "delay": _number(fields[-3]),
            }
        )

    return {
        "synchronized": bool(synchronized),
        "stratum": stratum,
        "reference": normalize(reference) if reference else None,
        "associations": associations,
    }


def evaluate_ntp(
    state: Mapping[str, Any], expected: Sequence[str], options: Mapping[str, Any]
) -> Verdict:
    """Judge whether NTP is actually working, not merely configured."""
    reasons: List[str] = []
    status = OK

    def worsen(level: str) -> None:
        nonlocal status
        if level == FAIL or (level == WARN and status == OK):
            status = level

    associations = {item["address"]: item for item in state["associations"]}

    if not state["synchronized"]:
        worsen(FAIL)
        reasons.append("clock is not synchronised")
    if state["stratum"] is not None and state["stratum"] >= 16:
        worsen(FAIL)
        reasons.append(f"stratum {state['stratum']} (unsynchronised)")

    for server in expected:
        item = associations.get(server)
        if item is None:
            worsen(FAIL)
            reasons.append(f"{server} is not associated")
        elif item["reach"] == 0:
            worsen(FAIL)
            reasons.append(f"{server} unreachable (reach 0)")
        elif item["reach"] != REACH_PERFECT:
            worsen(WARN)
            reasons.append(f"{server} missed polls (reach {item['reach_octal']})")

    if state["associations"] and not any(
        item["state"] == "sys.peer" for item in state["associations"]
    ):
        worsen(FAIL)
        reasons.append("no association selected as sys.peer")

    limit = options.get("max_offset")
    peer = next((i for i in state["associations"] if i["state"] == "sys.peer"), None)
    if limit is not None and peer and peer["offset"] is not None:
        if abs(peer["offset"]) > limit:
            worsen(WARN)
            reasons.append(f"offset {peer['offset']:.1f}ms exceeds {limit:g}ms")

    if status == OK:
        detail = f"synchronised to {state['reference'] or peer['address'] if peer else '?'}"
        if state["stratum"] is not None:
            detail += f", stratum {state['stratum']}"
        if peer and peer["offset"] is not None:
            detail += f", offset {peer['offset']:.1f}ms"
        return Verdict(OK, detail)
    return Verdict(status, "; ".join(reasons), reasons)


def add_ntp_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-s",
        "--servers",
        action="append",
        metavar="ADDR[,ADDR...]",
        help="the servers each device should be associated with. Defaults to "
        "ntp.servers in the standards file.",
    )
    parser.add_argument(
        "--max-offset",
        type=float,
        default=1000.0,
        metavar="MS",
        help="warn when the selected peer's offset exceeds this",
    )


def expected_ntp(args: argparse.Namespace) -> List[str]:
    if args.servers:
        raw = [chunk for value in args.servers for chunk in value.split(",")]
    else:
        raw = [host_and_port(item)["host"] for item in standards_of(args).entries("ntp.servers")]
    return [normalize(str(value)) for value in raw if str(value).strip()]


NTP = Check(
    name="ntp",
    help="are the NTP servers associated, reachable and selected?",
    platforms={
        "cisco_ios": CheckSupport(NTP_COMMANDS, parse_ntp_status, IOS_SAMPLE),
        "arista_eos": CheckSupport(NTP_COMMANDS, parse_ntp_status, EOS_SAMPLE),
    },
    evaluate=evaluate_ntp,
    add_arguments=add_ntp_arguments,
    expected=expected_ntp,
)

CHECKS: Dict[str, Check] = {NTP.name: NTP}
