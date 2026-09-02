"""NAC (802.1X / MAB) on access ports.

Every other feature here manages one setting for a whole device. This one is
per *interface*: a switch has a hundred access ports and the question is which
of them are missing part of the NAC block.

**The required configuration lives in `templates/<platform>/nac.j2`.** That
template is both the definition and the fix: the planner renders it once to
learn what a compliant port looks like, compares each in-scope interface
against that, and renders it again -- as `interface X` plus only the lines that
port is missing -- to correct it. There is one copy of the standard, and it is
in the place standards for a platform already live.

Which ports are in scope is a judgement, so it is stated rather than guessed:
physical interfaces, in `switchport mode access`, not shut, and not excluded by
name or description. A trunk or an uplink is not an access port and is never
touched.
"""

from __future__ import annotations

import argparse
import fnmatch
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import Desired, Entry, Feature, PlatformSupport, render, validate_word
from ..standards import StandardsError, of as standards_of

SHOW_COMMAND = "show running-config | section ^interface"

#: Interfaces that are not ports. No NAC question applies to them, so they are
#: never in scope whatever the standards file says.
NON_PHYSICAL = (
    "vlan",
    "loopback",
    "port-channel",
    "tunnel",
    "management",
    "null",
    "bdi",
    "nve",
    "virtual",
)

#: Descriptions that conventionally mean "this is not a user port". Only used
#: when the standards file does not say otherwise.
DEFAULT_EXCLUDE_DESCRIPTIONS = ("uplink", "trunk", "ap ", "access point")

IOS_SAMPLE = """\
interface GigabitEthernet1/0/1
 description User port
 switchport access vlan 10
 switchport mode access
 access-session host-mode multi-auth
 access-session closed
 access-session port-control auto
 mab
 dot1x pae authenticator
 dot1x timeout tx-period 7
 service-policy type control subscriber NAC-POLICY
 spanning-tree portfast
 spanning-tree bpduguard enable
interface GigabitEthernet1/0/2
 description User port
 switchport mode access
 mab
interface GigabitEthernet1/0/3
 description User port
 switchport mode access
 shutdown
interface GigabitEthernet1/0/48
 description uplink to core
 switchport mode trunk
interface Vlan10
 ip address 10.1.10.2 255.255.255.0
"""

EOS_SAMPLE = """\
interface Ethernet1
   description User port
   switchport mode access
   dot1x pae authenticator
   dot1x reauthentication
   dot1x port-control auto
interface Ethernet2
   description User port
   switchport mode access
interface Ethernet48
   description uplink to spine
   switchport mode trunk
"""


# --------------------------------------------------------------------------- #
# reading the interfaces
# --------------------------------------------------------------------------- #


def is_physical(name: str) -> bool:
    lowered = name.lower()
    return not any(lowered.startswith(prefix) for prefix in NON_PHYSICAL)


def parse_interfaces(output: str) -> List[Entry]:
    """One entry per interface, carrying its configuration lines."""
    entries: List[Entry] = []
    name: Optional[str] = None
    body: List[str] = []

    def flush() -> None:
        if name is None:
            return
        mode = "unknown"
        description = ""
        for line in body:
            if line.startswith("switchport mode "):
                mode = line.split()[-1]
            elif line.startswith("description "):
                description = line[len("description ") :].strip()
        entries.append(
            Entry(
                key=f"interface:{name}",
                line=f"interface {name}",
                display=f"interface {name} ({mode}{', shut' if 'shutdown' in body else ''})",
                data={
                    "name": name,
                    "lines": list(body),
                    "mode": mode,
                    "description": description,
                    "shutdown": "shutdown" in body,
                    "physical": is_physical(name),
                },
            )
        )

    for raw in output.splitlines():
        stripped = raw.strip()
        if not stripped or stripped == "!":
            continue
        if not raw.startswith((" ", "\t")):
            if stripped.startswith("interface "):
                flush()
                name = stripped.split(None, 1)[1].strip()
                body = []
            else:
                flush()
                name, body = None, []
            continue
        if name is not None:
            body.append(stripped)
    flush()
    return entries


# --------------------------------------------------------------------------- #
# which ports the standard applies to
# --------------------------------------------------------------------------- #


def in_scope(entry: Entry, rules: Mapping[str, Any]) -> Tuple[bool, str]:
    """Whether this interface should have NAC, and why not when it should not."""
    data = entry.data
    if not data["physical"]:
        return False, "not a physical port"
    for pattern in rules.get("exclude") or ():
        if fnmatch.fnmatch(data["name"], pattern) or data["name"].lower().startswith(
            str(pattern).lower()
        ):
            return False, f"excluded by name ({pattern})"
    description = (data["description"] or "").lower()
    for needle in rules.get("exclude_description") or ():
        if str(needle).lower() in description:
            return False, f"excluded by description ({needle})"
    if rules.get("access_only", True) and data["mode"] != "access":
        return False, f"not an access port (mode {data['mode']})"
    if rules.get("skip_shutdown", True) and data["shutdown"]:
        return False, "shut down"
    return True, ""


def required_lines(platform: str, variables: Mapping[str, Any]) -> List[str]:
    """What a compliant port looks like, from the template itself.

    Rendered in `declare` mode, which emits the block with no interface header,
    so there is exactly one copy of the standard and it is the one that gets
    pushed.
    """
    return render("nac", platform, [], [], {**variables, "declare": True})


def plan_nac(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    context = context or {}
    variables = context.get("variables") or {}
    platform = context.get("platform", "")
    notes = context.get("notes")
    rules = variables.get("scope") or {}

    wanted = required_lines(platform, variables)
    missing: Dict[str, List[str]] = {}
    audited = 0

    for entry in current:
        included, _ = in_scope(entry, rules)
        if not included:
            continue
        audited += 1
        configured = set(entry.data["lines"])
        absent = [line for line in wanted if line not in configured]
        if absent:
            missing[entry.data["name"]] = absent

    # The template renders from this; the runner gives each host its own copy.
    variables["missing"] = missing
    variables["audited"] = audited

    # A note, not an advisory: how many ports were looked at is reassurance,
    # and the commands themselves already say what is not compliant.
    if notes is not None and audited:
        notes.append(
            f"{len(missing)} of {audited} access port(s) are missing NAC configuration"
            if missing
            else f"{audited} access port(s) checked, all compliant"
        )

    return sorted(missing), []


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def reverse(commands, current, removed, context):
    """Remove the lines that were added, from the ports they were added to.

    The commands include `interface X` context lines; negating those would
    delete the interface configuration wholesale rather than undo a setting.
    """
    from ..rollback import Reversal

    missing = (context.get("variables") or {}).get("missing") or {}
    reversal = Reversal()
    for name in sorted(missing):
        reversal.commands.append(f"interface {name}")
        reversal.commands.extend(f" no {line}" for line in missing[name])
    return reversal


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--policy",
        help="subscriber control policy name; defaults to nac.policy in the "
        "standards file",
    )
    parser.add_argument(
        "--include-trunks",
        action="store_true",
        help="audit trunk ports too. Off by default: a trunk is not an access "
        "port, and NAC on an uplink takes the switch off the network.",
    )
    parser.add_argument(
        "--include-shutdown",
        action="store_true",
        help="audit administratively shut ports as well",
    )


def build_desired(args: argparse.Namespace) -> Desired:
    standards = standards_of(args)
    section = standards.section("nac")
    scope = dict(section.get("scope") or {})

    policy = args.policy or section.get("policy")
    if policy:
        policy = validate_word(str(policy), "policy name")

    if args.include_trunks:
        scope["access_only"] = False
    if args.include_shutdown:
        scope["skip_shutdown"] = False
    scope.setdefault("access_only", True)
    scope.setdefault("skip_shutdown", True)
    scope.setdefault("exclude", [])
    scope.setdefault("exclude_description", list(DEFAULT_EXCLUDE_DESCRIPTIONS))

    for key in ("exclude", "exclude_description"):
        if not isinstance(scope[key], (list, tuple)):
            raise StandardsError(f"nac.scope.{key} must be a list")

    return Desired(
        keys=[],  # worked out per device: which ports exist is the device's business
        variables={
            "policy": policy,
            "scope": scope,
            "missing": {},
            "audited": 0,
            # The template has two modes; the normal one is not `declare`.
            "declare": False,
        },
    )


FEATURE = Feature(
    name="nac",
    help="audit access ports for NAC (802.1X / MAB) configuration",
    platforms={
        "cisco_ios": PlatformSupport(SHOW_COMMAND, parse_interfaces, IOS_SAMPLE),
        "arista_eos": PlatformSupport(SHOW_COMMAND, parse_interfaces, EOS_SAMPLE),
    },
    add_arguments=add_arguments,
    build_desired=build_desired,
    plan=plan_nac,
    reverse=reverse,
    # The desired set is every in-scope port on the device, which is not known
    # until the device has been read, so verification re-runs the planner.
    verify_with_plan=True,
    selftest_args=[],
)
