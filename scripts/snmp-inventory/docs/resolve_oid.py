#!/usr/bin/env python3
"""Resolve MIB object names to numeric OIDs by parsing MIB files directly.

Used to derive every vendor OID in snmpinv/vendors.py. Kept in the tree so the
numbers in OID-SOURCES.md can be re-checked rather than taken on trust.

`snmptranslate` would be the obvious tool and cannot be used here: Debian and
Ubuntu ship net-snmp without the IETF base MIBs, so vendor MIBs fail to resolve
their IMPORTS on a stock box.

IMPORTANT — pass one vendor's MIBs at a time. MIB object names are scoped to
their module, not global. Parsing several vendors together lets a name defined
in one module satisfy a reference in another: doing exactly that once made
Check Point's svnVersion resolve into Aruba's enterprise tree (14823) instead
of its own (2620). Give each vendor its own directory.

Usage:
    ./resolve_oid.py <mib-dir> <name> [<name> ...]

    mkdir -p ~/mibs/checkpoint && cd ~/mibs/checkpoint
    curl -fsSLO https://raw.githubusercontent.com/librenms/librenms/master/mibs/checkpoint/CHECKPOINT-MIB
    ./resolve_oid.py ~/mibs/checkpoint svnVersion
    svnVersion    1.3.6.1.4.1.2620.1.6.4.1

Unresolved names print the anchor they are waiting on, which is usually a root
defined in a separate SMI MIB you also need to download.
"""

from __future__ import annotations

import glob
import os
import re
import sys

# Roots that every MIB assumes rather than defines.
WELL_KNOWN = {
    "iso": "1", "org": "1.3", "dod": "1.3.6", "internet": "1.3.6.1",
    "directory": "1.3.6.1.1", "mgmt": "1.3.6.1.2", "mib-2": "1.3.6.1.2.1",
    "transmission": "1.3.6.1.2.1.10", "experimental": "1.3.6.1.3",
    "private": "1.3.6.1.4", "enterprises": "1.3.6.1.4.1",
    "snmpV2": "1.3.6.1.6", "snmpModules": "1.3.6.1.6.3",
    "system": "1.3.6.1.2.1.1", "interfaces": "1.3.6.1.2.1.2", "ip": "1.3.6.1.2.1.4",
}

_DEFINITION = re.compile(
    r"^[ \t]*([a-zA-Z][\w-]*)[ \t]+(?:OBJECT-TYPE|OBJECT[ \t]+IDENTIFIER|MODULE-IDENTITY|"
    r"OBJECT-IDENTITY|NOTIFICATION-TYPE|OBJECT-GROUP|MODULE-COMPLIANCE|NOTIFICATION-GROUP)"
    r"(.*?)::=[ \t]*\{([^}]*)\}",
    re.S | re.M,
)
_PLAIN = re.compile(r"^[ \t]*([a-zA-Z][\w-]*)[ \t]*::=[ \t]*\{([^}]*)\}", re.M)
_NAMED_NUMBER = re.compile(r"([a-zA-Z][\w-]*)\((\d+)\)")


def parse(mib_dir: str) -> dict[str, list[str]]:
    """Collect every `name ::= { parent N }` assignment in a directory."""
    assignments: dict[str, list[str]] = {}
    for path in sorted(glob.glob(os.path.join(mib_dir, "*"))):
        if not os.path.isfile(path) or path.endswith((".py", ".md")):
            continue
        text = re.sub(r"--.*?$", "", open(path, errors="replace").read(), flags=re.M)
        # Strip IMPORTS sections before matching. When a MIB writes its imports
        # on one line — "IMPORTS MODULE-IDENTITY, OBJECT-TYPE, enterprises" —
        # the definition regex reads it as a definition named IMPORTS, and its
        # lazy (.*?)::= then swallows everything up to the file's FIRST real
        # assignment, deleting that assignment from the parse. Infoblox's
        # IB-SMI-MIB is written exactly this way: `infoblox ::= { enterprises
        # 7779 }` was eaten and the entire 7779 subtree failed to resolve.
        # Same failure class as the svnVersion incident in the module
        # docstring: the parser quietly misreading structure it was never
        # pointed at.
        text = re.sub(r"\bIMPORTS\b.*?;", "", text, flags=re.S)
        for match in _DEFINITION.finditer(text):
            assignments.setdefault(match.group(1), match.group(3).split())
        for match in _PLAIN.finditer(text):
            assignments.setdefault(match.group(1), match.group(2).split())
    return assignments


def resolve_all(assignments: dict[str, list[str]]) -> tuple[dict[str, str], dict[str, int]]:
    """Resolve to numeric OIDs by repeated passes until nothing more resolves."""
    resolved = dict(WELL_KNOWN)
    missing: dict[str, int] = {}
    for _ in range(60):
        progressed = False
        for name, tokens in assignments.items():
            if name in resolved:
                continue
            parts: list[str] = []
            ok = True
            for token in tokens:
                token = token.strip()
                if not token:
                    continue
                named = _NAMED_NUMBER.fullmatch(token)
                if named:
                    parts.append(named.group(2))
                elif token.isdigit():
                    parts.append(token)
                elif not parts:
                    if token in resolved:
                        parts.append(resolved[token])
                    else:
                        missing[token] = missing.get(token, 0) + 1
                        ok = False
                        break
                else:
                    ok = False
                    break
            if ok and parts:
                resolved[name] = ".".join(parts)
                progressed = True
        if not progressed:
            break
    return resolved, missing


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2
    mib_dir, names = argv[0], argv[1:]
    if not os.path.isdir(mib_dir):
        sys.stderr.write(f"not a directory: {mib_dir}\n")
        return 2

    assignments = parse(mib_dir)
    resolved, missing = resolve_all(assignments)
    print(f"# {len(assignments)} definitions parsed from {mib_dir}, {len(resolved)} resolved\n")

    unresolved = []
    for name in names:
        value = resolved.get(name)
        print(f"{name:34s} {value or '<unresolved>'}")
        if value is None:
            unresolved.append(name)

    if unresolved:
        print("\n# Unresolved. Most likely a root defined in an SMI MIB that is not in")
        print("# this directory. The anchors being waited on, most-referenced first:")
        for anchor, count in sorted(missing.items(), key=lambda kv: -kv[1])[:12]:
            print(f"#   {anchor} ({count} references)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
