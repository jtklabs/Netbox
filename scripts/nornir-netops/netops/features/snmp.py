"""SNMP: v3 users, groups, views, trap hosts, and the absence of v2c.

Two things make this feature different from the others.

**SNMPv3 users are not in the running config on IOS.** `show running-config`
never shows `snmp-server user`, so the check reads `show snmp user` as well and
the parser takes users from whichever of the two produced them (EOS does write
them into the config). `show snmp user` reports the group and the auth/privacy
protocols but never the passwords, so a password change cannot be detected --
an existing user is rewritten when its group or its protocols differ from the
standard, and left alone when they match.

**`communities: []` is a statement.** The standard here is that no v2c
community exists, so any community found on a device is removed -- in `--add`
mode too, because "there must be none" is not an extra to be left alone. If the
standards file says nothing about communities, none are touched.

**Each user may name its own ACL.** `snmp.acl` is the default for everything;
a `acl:` on an individual user or group overrides it, which is how one poller
gets one allow list and another gets a different one. A group's ACL is written
into the running config and is therefore compared like any other field. A
*user's* is not: `show snmp user` reports the group and the protocols and
nothing else, so on IOS the binding can be set but never read back. It is
written whenever the user is written, and `--rewrite-users` is the way to push
a changed one without waiting for some other field to differ.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import (
    MODE_REPLACE,
    Desired,
    Entry,
    Feature,
    PlatformSupport,
    normalize,
    validate_address,
    validate_secret_value,
    validate_text,
    validate_word,
)
from ..standards import StandardsError, host_and_port, of as standards_of

CONFIG_COMMAND = "show running-config | include ^snmp-server"
USER_COMMAND = "show snmp user"

#: Shortest SNMPv3 passphrase the platforms accept.
MIN_PASSPHRASE_LENGTH = 8

SECURITY_LEVELS = ("noauth", "auth", "priv")

IOS_SAMPLE = """\
snmp-server community public RO
snmp-server view NMS-VIEW iso included
snmp-server group NMS-RO v3 priv read NMS-VIEW access SNMP-POLLERS
snmp-server location OLD LOCATION
snmp-server host 10.1.1.50 version 3 priv nmsuser
User name: nmsuser
Engine ID: 800000090300AABBCCDDEEFF
storage-type: nonvolatile        active
Authentication Protocol: MD5
Privacy Protocol: DES
Group-name: NMS-RO
"""

EOS_SAMPLE = """\
snmp-server view NMS-VIEW iso included
snmp-server group NMS-RO v3 priv read NMS-VIEW
snmp-server location ATL DC1 - row 4
snmp-server contact netops@example.com
snmp-server user nmsuser NMS-RO v3 auth sha <hash> priv aes128 <hash>
"""


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def _protocol(value: Optional[str]) -> Optional[str]:
    """`AES128`, `aes 128` and `aes-128` are the same privacy protocol."""
    if not value:
        return None
    return re.sub(r"[\s_-]+", "", str(value)).lower()


#: AES key lengths, which IOS writes as a separate token: `priv aes 128`.
KEY_LENGTHS = ("128", "192", "256")


def _protocol_at(tokens: Sequence[str], index: int) -> Optional[str]:
    """Read a protocol that may be spelled as two tokens.

    Only a known key length is joined on: a passphrase that happens to be all
    digits must not be mistaken for part of the protocol name.
    """
    if index >= len(tokens):
        return None
    value = tokens[index]
    if index + 1 < len(tokens) and tokens[index + 1] in KEY_LENGTHS:
        value += tokens[index + 1]
    return _protocol(value)


def _fields(tokens: Sequence[str], keys: Sequence[str]) -> Dict[str, str]:
    """Pull `keyword value` pairs out of a config line."""
    found: Dict[str, str] = {}
    for index, token in enumerate(tokens):
        if token in keys and index + 1 < len(tokens):
            found[token] = tokens[index + 1]
    return found


def parse_snmp(output: str) -> List[Entry]:
    """Parse `show running-config | include ^snmp-server` and `show snmp user`.

    The two formats are unmistakable -- one starts every line with
    `snmp-server`, the other is a block of `Key: value` lines -- so they can be
    handed in together.
    """
    entries: List[Entry] = []
    users: Dict[str, Dict[str, Any]] = {}
    block: Dict[str, str] = {}

    def close_block() -> None:
        name = block.get("user name")
        if name:
            users.setdefault(name, {}).update(
                {
                    "group": block.get("group-name"),
                    "auth": _protocol(block.get("authentication protocol")),
                    "priv": _protocol(block.get("privacy protocol")),
                }
            )
        block.clear()

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue

        # --- `show snmp user` block form ---
        if ":" in line and not line.startswith("snmp-server"):
            key, _, value = line.partition(":")
            key = key.strip().lower()
            if key == "user name":
                close_block()
            if key in (
                "user name",
                "group-name",
                "authentication protocol",
                "privacy protocol",
            ):
                block[key] = value.strip().split()[0] if value.strip() else ""
            continue

        if not line.startswith("snmp-server "):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        kind = tokens[1]

        if kind == "community" and len(tokens) >= 3:
            entries.append(
                Entry(
                    key=f"community:{tokens[2]}",
                    line=f"snmp-server community {tokens[2]}",
                    display=f"snmp-server community <redacted> ({' '.join(tokens[3:]) or 'RO'})",
                    # The string has to be named to be removed, and it is a
                    # credential: flag it so the command is scrubbed on the way out.
                    data={"kind": "community", "secret_value": tokens[2]},
                )
            )
        elif kind == "view" and len(tokens) >= 5:
            entries.append(
                Entry(
                    key=f"view:{tokens[2]}",
                    line=f"snmp-server view {tokens[2]} {tokens[3]} {tokens[4]}",
                    data={"kind": "view", "oid": tokens[3], "action": tokens[4]},
                )
            )
        elif kind == "group" and len(tokens) >= 3:
            found = _fields(tokens, ("read", "write", "notify", "access"))
            security = next((t for t in tokens if t in SECURITY_LEVELS), "noauth")
            entries.append(
                Entry(
                    key=f"group:{tokens[2]}",
                    line=line,
                    data={
                        "kind": "group",
                        "security": security,
                        "read": found.get("read"),
                        "write": found.get("write"),
                        "access": found.get("access"),
                    },
                )
            )
        elif kind == "host" and len(tokens) >= 3:
            security = next((t for t in tokens if t in SECURITY_LEVELS), None)
            version = None
            if "version" in tokens:
                index = tokens.index("version")
                if index + 1 < len(tokens):
                    version = tokens[index + 1]
            user = tokens[-1] if tokens[-1] not in SECURITY_LEVELS else None
            entries.append(
                Entry(
                    key=f"host:{normalize(tokens[2])}",
                    line=line,
                    data={
                        "kind": "host",
                        "version": version,
                        "security": security,
                        "user": user,
                    },
                )
            )
        elif kind == "user" and len(tokens) >= 4:
            # EOS writes v3 users into the running config; IOS does not.
            record: Dict[str, Any] = {"group": tokens[3], "auth": None, "priv": None}
            for label in ("auth", "priv"):
                if label in tokens:
                    record[label] = _protocol_at(tokens, tokens.index(label) + 1)
            users.setdefault(tokens[2], {}).update(record)
        elif kind in ("location", "contact", "chassis-id") and len(tokens) >= 3:
            value = " ".join(tokens[2:])
            entries.append(
                Entry(
                    key=f"{kind}:{value}",
                    line=f"snmp-server {kind} {value}",
                    data={"kind": kind, "value": value},
                )
            )
    close_block()

    for name, record in users.items():
        detail = " ".join(
            part
            for part in (
                f"group {record.get('group')}" if record.get("group") else "",
                f"auth {record['auth']}" if record.get("auth") else "",
                f"priv {record['priv']}" if record.get("priv") else "",
            )
            if part
        )
        entries.append(
            Entry(
                key=f"user:{name}",
                line=f"snmp-server user {name} {record.get('group') or ''} v3".strip(),
                display=f"snmp-server user {name} ({detail or 'no auth'})",
                data={"kind": "user", **record},
            )
        )
    return entries


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #

#: Which fields have to match for an existing item to be left alone. Only the
#: fields the standard actually states are compared, so a device attribute this
#: tool does not manage never forces a rewrite.
COMPARED = {
    "user": ("group", "auth", "priv"),
    "group": ("security", "read", "write", "access"),
    "view": ("oid", "action"),
    "host": ("version", "security", "user"),
}


def _differs(current: Mapping[str, Any], wanted: Mapping[str, Any], fields: Sequence[str]) -> bool:
    for field in fields:
        expected = wanted.get(field)
        if expected is None:
            continue  # the standard says nothing about this field
        if str(current.get(field) or "") != str(expected):
            return True
    return False


def plan_snmp(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    context = context or {}
    variables = context.get("variables") or {}
    wanted: Mapping[str, Any] = variables.get("entries", {})
    ignores = context.get("ignores") or ()
    configured = {entry.key: entry for entry in current}

    to_add: List[str] = []
    to_remove: List[Entry] = []

    for key in desired:
        entry = configured.get(key)
        if entry is None:
            to_add.append(key)
            continue
        kind = wanted[key]["kind"]
        if kind == "user" and variables.get("rewrite_users"):
            # Asked for explicitly: there is nothing to compare a passphrase or
            # a user's ACL against, so this is the only way to push either.
            to_remove.append(entry)
            to_add.append(key)
            continue
        fields = [f for f in COMPARED.get(kind, ()) if f not in ignores]
        if fields and _differs(entry.data, wanted[key], fields):
            # A v3 user cannot be edited into a different group or protocol, and
            # its passphrase is not readable, so it is removed and written back.
            to_remove.append(entry)
            to_add.append(key)

    # "There must be no community" is a standard, not an extra to leave alone.
    if variables.get("forbid_communities"):
        to_remove.extend(e for e in current if e.data.get("kind") == "community")

    if mode == MODE_REPLACE:
        managed_kinds = {"user", "group", "view", "host"}
        for key, entry in configured.items():
            if key in desired or entry.data.get("kind") not in managed_kinds:
                continue
            to_remove.append(entry)

    return to_add, to_remove


# --------------------------------------------------------------------------- #
# passphrases
# --------------------------------------------------------------------------- #


def passphrase_variables(user: str) -> Tuple[str, str]:
    stem = re.sub(r"[^A-Za-z0-9]", "_", user).upper()
    return f"NETOPS_SNMP_AUTH_{stem}", f"NETOPS_SNMP_PRIV_{stem}"


def resolve_passphrases(
    users: Sequence[Mapping[str, Any]], args: argparse.Namespace
) -> Dict[str, Dict[str, str]]:
    """Auth and privacy passphrases, from AWS or the environment.

    Resolved for every managed user up front, because which devices are missing
    which user is not known until each one has been read -- and a dry run has to
    be able to show the command it would send.
    """
    from ..credentials import CredentialError, fetch_json_secret

    secret: Mapping[str, Any] = {}
    if args.passphrase_secret:
        secret = fetch_json_secret(args.passphrase_secret, args.aws_region)

    resolved: Dict[str, Dict[str, str]] = {}
    for user in users:
        name = user["name"]
        auth_var, priv_var = passphrase_variables(name)
        record = secret.get(name) if isinstance(secret.get(name), Mapping) else {}

        values = {
            "auth": record.get("auth") or os.environ.get(auth_var),
            "priv": record.get("priv") or os.environ.get(priv_var),
        }
        for kind, variable in (("auth", auth_var), ("priv", priv_var)):
            if not user.get(kind):
                values[kind] = None
                continue
            if not values[kind] and sys.stdin.isatty():
                import getpass

                values[kind] = getpass.getpass(f"SNMPv3 {kind} passphrase for {name}: ")
            if not values[kind]:
                raise CredentialError(
                    f"no SNMPv3 {kind} passphrase for {name!r}: put it in ${variable}, "
                    f"in the --passphrase-secret JSON, or run interactively"
                )
            validate_secret_value(values[kind], f"{kind} passphrase for {name}")
            if len(values[kind]) < MIN_PASSPHRASE_LENGTH:
                raise ValueError(
                    f"the {kind} passphrase for {name} is shorter than "
                    f"{MIN_PASSPHRASE_LENGTH} characters"
                )
        resolved[name] = {k: v for k, v in values.items() if v}
    return resolved


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def selftest_placeholders(standards) -> Dict[str, str]:
    """Placeholder passphrases for whichever users the standards file names.

    Derived rather than hardcoded: adding a user to the file should not also
    mean editing this module for the offline render to keep working.
    """
    placeholders: Dict[str, str] = {}
    for item in standards.entries("snmp.users"):
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        for variable in passphrase_variables(str(item["name"])):
            placeholders[variable] = "selftest-placeholder"
    return placeholders


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--passphrase-secret",
        metavar="NAME_OR_ARN",
        default=os.environ.get("NETOPS_SNMP_SECRET"),
        help='AWS secret holding {"user": {"auth": "...", "priv": "..."}} '
        "[$NETOPS_SNMP_SECRET]",
    )
    parser.add_argument(
        "--location", help="override snmp.location from the standards file"
    )
    parser.add_argument("--contact", help="override snmp.contact from the standards file")
    parser.add_argument(
        "--rewrite-users",
        action="store_true",
        help="negate and recreate every managed v3 user, whether or not it looks "
        "different. The way to push a changed passphrase or a changed per-user "
        "ACL, neither of which can be read back from a device.",
    )


def _acl_names(standards) -> Optional[set]:
    """The ACLs the standards file defines, or None if it defines none.

    Used to catch a typo before it binds SNMP to an access list that does not
    exist. If the file has no `acls:` section at all the ACLs are managed
    somewhere else, and naming one here is not this tool's business to police.
    """
    defined = standards.value("acls")
    if not defined:
        return None
    names = set()
    for item in defined:
        if isinstance(item, Mapping) and item.get("name"):
            names.add(str(item["name"]))
    return names or None


def build_desired(args: argparse.Namespace) -> Desired:
    standards = standards_of(args)
    section = standards.section("snmp")
    if not section:
        raise ValueError("no snmp section in the standards file")

    known_acls = _acl_names(standards)

    def acl_for(item: Mapping[str, Any], what: str) -> Optional[str]:
        """This item's own ACL, else the section default, else nothing."""
        name = item.get("acl", section.get("acl"))
        if not name:
            return None
        name = validate_word(str(name), "ACL name")
        if known_acls is not None and name not in known_acls:
            raise StandardsError(
                f"{what} names ACL {name!r}, which the acls: section does not "
                f"define (defined: {', '.join(sorted(known_acls))}). Binding SNMP "
                f"to an access list that does not exist is worse than not binding it."
            )
        return name

    acl = section.get("acl")
    if acl:
        acl = validate_word(str(acl), "ACL name")

    keys: List[str] = []
    entries: Dict[str, Dict[str, Any]] = {}

    def add(key: str, record: Dict[str, Any]) -> None:
        if key not in entries:
            keys.append(key)
            entries[key] = record

    for item in standards.entries("snmp.views"):
        name = validate_word(str(item["name"]), "view name")
        add(
            f"view:{name}",
            {
                "kind": "view",
                "name": name,
                "oid": validate_word(str(item.get("oid", "iso")), "oid"),
                "action": validate_word(str(item.get("action", "included")), "view action"),
            },
        )

    for item in standards.entries("snmp.groups"):
        name = validate_word(str(item["name"]), "group name")
        security = str(item.get("security", "priv"))
        if security not in SECURITY_LEVELS:
            raise StandardsError(
                f"snmp group {name}: security must be one of {', '.join(SECURITY_LEVELS)}"
            )
        add(
            f"group:{name}",
            {
                "kind": "group",
                "name": name,
                "security": security,
                "read": validate_word(str(item["read"]), "view name") if item.get("read") else None,
                "write": validate_word(str(item["write"]), "view name")
                if item.get("write")
                else None,
                "access": acl_for(item, f"snmp group {name}"),
            },
        )

    users: List[Dict[str, Any]] = []
    for item in standards.entries("snmp.users"):
        name = validate_word(str(item["name"]), "user name")
        record = {
            "kind": "user",
            "name": name,
            "group": validate_word(str(item["group"]), "group name"),
            "auth": _protocol(item.get("auth")),
            "priv": _protocol(item.get("priv")),
            "access": acl_for(item, f"snmp user {name}"),
        }
        users.append(record)
        add(f"user:{name}", record)

    for item in standards.entries("snmp.hosts"):
        record = host_and_port(item)
        host = validate_address(str(record["host"]))
        add(
            f"host:{normalize(host)}",
            {
                "kind": "host",
                "host": normalize(host),
                "version": str(record.get("version", "3")),
                "security": str(record.get("security", "priv")),
                "user": validate_word(str(record["user"]), "user name")
                if record.get("user")
                else None,
            },
        )

    for kind, override in (("location", args.location), ("contact", args.contact)):
        value = override or section.get(kind)
        if value:
            text = validate_text(str(value), kind)
            add(f"{kind}:{text}", {"kind": kind, "value": text})
    if section.get("chassis_id"):
        text = validate_text(str(section["chassis_id"]), "chassis-id")
        add(f"chassis-id:{text}", {"kind": "chassis-id", "value": text})

    if not keys and not standards.defined("snmp.communities"):
        raise ValueError("the snmp section of the standards file configures nothing")

    passphrases = resolve_passphrases(users, args) if users else {}
    for record in users:
        record["passphrases"] = passphrases.get(record["name"], {})

    communities = standards.entries("snmp.communities") if standards.defined(
        "snmp.communities"
    ) else None
    if communities:
        raise StandardsError(
            "snmp.communities may only be empty: this tool removes v2c communities, "
            "it does not configure them"
        )

    return Desired(
        keys=keys,
        variables={
            "entries": entries,
            "acl": acl,
            "forbid_communities": communities is not None,
            "rewrite_users": bool(getattr(args, "rewrite_users", False)),
        },
        secrets=[value for record in passphrases.values() for value in record.values()],
    )


FEATURE = Feature(
    name="snmp",
    help="converge SNMPv3 users, groups, views and hosts, and remove v2c communities",
    platforms={
        "cisco_ios": PlatformSupport(
            CONFIG_COMMAND, parse_snmp, IOS_SAMPLE, extra_commands=(USER_COMMAND,)
        ),
        # EOS restricts SNMP with a control-plane ACL, not `access` on a group.
        "arista_eos": PlatformSupport(
            CONFIG_COMMAND, parse_snmp, EOS_SAMPLE, ignores=("access",)
        ),
    },
    add_arguments=add_arguments,
    build_desired=build_desired,
    plan=plan_snmp,
    selftest_args=[],
    selftest_env_from=selftest_placeholders,
)
