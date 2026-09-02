"""Access lists, with order enforced.

An ACL is an ordered list, not a set: `permit 10.1.1.0/24` before `deny any`
and after it mean different things. So entries are compared positionally, and a
device whose entries differ in content *or* order has that ACL rebuilt to
match.

Rebuilding means `no ip access-list standard NAME` immediately followed by the
new definition, in the same config push. There is no way to reorder in place
that is not worse. The gap is a few milliseconds inside one session, but it is
a real gap: while it is open the ACL does not exist, and anything referencing
it behaves as that platform behaves with a missing ACL.

Whether that is acceptable depends entirely on the ACL. Dropping an SNMP poller
list for a moment costs a missed poll; dropping the ACL on a VTY line or an
edge interface is a different question with a different answer. So each ACL in
the standards file decides for itself with `rebuild: true`. Without it an ACL
that is *missing* is still created -- there is nothing to delete, so there is no
gap -- but one that exists and has drifted is reported for a human rather than
rewritten.

--replace deliberately does nothing extra here. Making a device's ACLs exactly
the file's list would delete every ACL this file does not mention -- VTY, NAT,
route-map -- which is not a thing to offer as a flag.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import (
    Desired,
    Entry,
    Feature,
    InvalidValue,
    PlatformSupport,
    render,
    validate_word,
)
from ..standards import StandardsError, of as standards_of

SHOW_COMMAND = "show running-config | section ^ip access-list standard"

#: Only standard ACLs for now. An extended ACL needs protocol and port
#: handling, and guessing at it would be worse than saying so.
SUPPORTED_TYPES = ("standard",)

_SEQUENCE = re.compile(r"^\d+\s+")

IOS_SAMPLE = """\
ip access-list standard SNMP-POLLERS
 10 permit 10.1.1.0 0.0.0.255
 20 deny   any log
ip access-list standard VTY-ACCESS
 10 permit 10.1.1.0 0.0.0.255
"""

EOS_SAMPLE = """\
ip access-list standard SNMP-POLLERS
   10 permit 10.2.0.0/16
   20 permit 10.1.1.0/24
   30 deny any log
"""


def parse_entry(text: str) -> Dict[str, Any]:
    """One ACL line in a form both platforms can be compared in.

    `permit 10.1.1.0 0.0.0.255` (IOS) and `permit 10.1.1.0/24` (EOS) are the
    same rule, so both normalize to the CIDR form.
    """
    line = _SEQUENCE.sub("", text.strip())
    tokens = line.split()
    if not tokens or tokens[0] not in ("permit", "deny"):
        raise InvalidValue(f"{text.strip()!r} is not a permit or deny entry")

    action = tokens[0]
    rest = tokens[1:]
    log = False
    if rest and rest[-1] == "log":
        log = True
        rest = rest[:-1]
    if not rest:
        raise InvalidValue(f"{text.strip()!r} has nothing to match on")

    if rest[0] == "any":
        target = "any"
    elif rest[0] == "host" and len(rest) > 1:
        target = f"{rest[1]}/32"
    elif len(rest) >= 2 and "/" not in rest[0]:
        # IOS: address followed by a wildcard mask.
        try:
            bits = int(ipaddress.IPv4Address(rest[1])) ^ 0xFFFFFFFF
            network = ipaddress.ip_network(
                f"{rest[0]}/{ipaddress.IPv4Address(bits)}", strict=False
            )
        except ValueError as exc:
            raise InvalidValue(f"{text.strip()!r}: {exc}") from exc
        target = str(network)
    else:
        try:
            target = str(ipaddress.ip_network(rest[0], strict=False))
        except ValueError as exc:
            raise InvalidValue(f"{text.strip()!r}: {exc}") from exc

    return {"action": action, "target": target, "log": log}


def _signature(entries: Sequence[Mapping[str, Any]]) -> Tuple:
    """Comparable form of a whole ACL: ordered, so a reordering shows up."""
    return tuple((e["action"], e["target"], e["log"]) for e in entries)


def parse_acls(output: str) -> List[Entry]:
    """One entry per ACL, carrying its entries in the order the device has them."""
    entries: List[Entry] = []
    name: Optional[str] = None
    body: List[Dict[str, Any]] = []

    def flush() -> None:
        if name is None:
            return
        entries.append(
            Entry(
                key=name,
                line=f"ip access-list standard {name}",
                display=f"ip access-list standard {name} ({len(body)} entr"
                f"{'y' if len(body) == 1 else 'ies'})",
                data={"entries": list(body)},
            )
        )

    for raw in output.splitlines():
        if not raw.strip():
            continue
        if raw.startswith((" ", "\t")):
            if name is not None:
                try:
                    body.append(parse_entry(raw))
                except InvalidValue:
                    continue  # a remark or something we do not model
            continue
        flush()
        name, body = None, []
        tokens = raw.strip().split()
        if len(tokens) >= 4 and tokens[:3] == ["ip", "access-list", "standard"]:
            name = tokens[3]
    flush()
    return entries


def plan_acls(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    context = context or {}
    wanted: Mapping[str, Any] = (context.get("variables") or {}).get("acls", {})
    configured = {entry.key: entry for entry in current}

    advisories: List[str] = context.get("advisories") if context.get("advisories") is not None else []

    to_add: List[str] = []
    to_remove: List[Entry] = []
    for name in desired:
        entry = configured.get(name)
        target = _signature(wanted[name]["entries"])
        if entry is None:
            to_add.append(name)  # nothing to delete, so nothing to expose
            continue
        if _signature(entry.data.get("entries", ())) == target:
            continue  # same entries, same order
        if not wanted[name].get("rebuild"):
            advisories.append(
                f"{name} has drifted ({len(entry.data.get('entries', ()))} entries on the "
                f"device, {len(wanted[name]['entries'])} in the standard). Rewriting it "
                f"means deleting it first, so it would not exist for a moment -- set "
                f"`rebuild: true` on {name} in the standards file if that is acceptable "
                f"for this ACL, or fix it by hand."
            )
            continue
        to_remove.append(entry)  # negate, then write it back
        to_add.append(name)
    return to_add, to_remove


def _acl_from_standards(item: Mapping[str, Any], standards) -> Dict[str, Any]:
    if "name" not in item:
        raise StandardsError(f"acl {item!r} has no 'name'")
    name = validate_word(str(item["name"]), "ACL name")
    kind = str(item.get("type", "standard"))
    if kind not in SUPPORTED_TYPES:
        raise StandardsError(
            f"acl {name}: type {kind!r} is not supported yet "
            f"(supported: {', '.join(SUPPORTED_TYPES)})"
        )

    entries: List[Dict[str, Any]] = []
    if item.get("entries"):
        for text in standards.resolve(item["entries"]):
            entries.append(parse_entry(str(text)))
    for network in standards.resolve(item.get("permit") or []):
        entries.append(parse_entry(f"permit {network}"))
    for network in standards.resolve(item.get("deny") or []):
        entries.append(parse_entry(f"deny {network}"))
    if item.get("deny_log"):
        entries.append(parse_entry("deny any log"))

    if not entries:
        raise StandardsError(f"acl {name} has no entries")
    return {
        "name": name,
        "type": kind,
        "entries": entries,
        # Whether this particular ACL may be deleted and rewritten to correct
        # its order. Off unless the file says otherwise.
        "rebuild": bool(item.get("rebuild", False)),
    }


def reverse(commands, current, removed, context):
    """Put each ACL back exactly as it was, entries and order included.

    Negating the commands one at a time would be wrong: they include the
    `ip access-list standard NAME` context line, and `no` on that deletes the
    whole list rather than undoing an entry.
    """
    from ..rollback import Reversal

    platform = context["platform"]
    existing = {entry.key: entry for entry in current}
    reversal = Reversal()

    for name in context.get("added") or []:
        before = existing.get(name)
        if before is None:
            # It did not exist; undoing means it should not exist.
            reversal.commands.append(f"no ip access-list standard {name}")
            continue
        restored = {
            "acls": {
                name: {
                    "name": name,
                    "type": "standard",
                    "entries": before.data.get("entries", []),
                    "rebuild": True,
                }
            }
        }
        reversal.commands.extend(render("acl", platform, [name], [before], restored))
    return reversal


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-a",
        "--acl",
        action="append",
        metavar="NAME",
        help="manage only these ACLs from the standards file; repeatable "
        "(default: all of them)",
    )


def build_desired(args: argparse.Namespace) -> Desired:
    standards = standards_of(args)
    defined = standards.value("acls") or []
    if not isinstance(defined, list):
        raise StandardsError("'acls' must be a list of ACL definitions")

    acls: Dict[str, Any] = {}
    for item in defined:
        if not isinstance(item, Mapping):
            raise StandardsError(f"acl {item!r} must be a mapping with a 'name'")
        record = _acl_from_standards(item, standards)
        acls[record["name"]] = record

    names = list(acls)
    if args.acl:
        wanted = [validate_word(name, "ACL name") for name in args.acl]
        missing = [name for name in wanted if name not in acls]
        if missing:
            raise ValueError(
                f"no ACL named {', '.join(missing)} in the standards file "
                f"(defined: {', '.join(names) or 'none'})"
            )
        names = wanted

    if not names:
        raise ValueError("no ACLs defined: add an 'acls:' section to the standards file")
    return Desired(keys=names, variables={"acls": acls})


FEATURE = Feature(
    name="acl",
    help="converge the access lists defined in the standards file, order included",
    platforms={
        "cisco_ios": PlatformSupport(SHOW_COMMAND, parse_acls, IOS_SAMPLE),
        "arista_eos": PlatformSupport(SHOW_COMMAND, parse_acls, EOS_SAMPLE),
    },
    add_arguments=add_arguments,
    build_desired=build_desired,
    plan=plan_acls,
    reverse=reverse,
    rollback_note=(
        "undoing this rebuilds each ACL as it was, which means deleting it "
        "again for a moment"
    ),
    selftest_args=[],
)
