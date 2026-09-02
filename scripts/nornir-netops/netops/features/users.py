"""Local users and password rotation.

Both IOS and EOS refuse to cleanly overwrite an existing account when the hash
type changes -- a `username x password 7 ...` will not simply become a
`username x secret 9 ...`. So every managed account is negated and rewritten in
the same config push: ``no username x`` immediately followed by the new
``username x ...``. That is also exactly what a rotation is, which is why this
feature always rewrites rather than trying to diff a salted hash it cannot
reproduce.

Passwords never appear on the command line. They come from AWS Secrets Manager,
from the environment (`.env`), or from a prompt -- and they are scrubbed out of
every command list, report and device echo before anyone sees them.

An EOS account can also carry an ssh-key, on its own `username x ssh-key ...`
line. That is an alternative credential which bypasses the password being
managed here, so it is negated explicitly -- before the account itself, while
the account still exists -- rather than trusting `no username x` to cascade.
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
    validate_secret_value,
    validate_word,
)

SHOW_COMMAND = "show running-config | include ^username"

#: Shortest plaintext password this tool will set. A rotation is the wrong
#: moment to weaken a fleet; a pre-computed hash (--hash-type) bypasses it.
MIN_PASSWORD_LENGTH = 8

#: Tokens that follow `secret`/`password` to name the hash type rather than
#: being the value itself.
SECRET_TYPES = {"0", "5", "7", "8", "9", "sha512"}

#: Types that are plaintext or trivially reversible. Surfaced in the report so
#: a dry run shows *why* an account is being rewritten.
WEAK_TYPES = {"password 0", "password 7", "secret 0"}

IOS_SAMPLE = """\
username admin privilege 15 secret 9 $9$Xq2vRs4Tu6Vw8x$abcdefghijklmnopqrstuv
username legacy privilege 15 password 7 070C285F4D06485744
username netauto privilege 15 secret 5 $1$saLt$0123456789abcdefghijkl
"""

EOS_SAMPLE = """\
username admin privilege 15 role network-admin secret sha512 $6$saLt$0123456789
username legacy privilege 15 secret 5 $1$saLt$abcdefghijklmnopqrst
username svc privilege 15 role network-operator nopassword
username svc ssh-key ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCexample
"""


def parse_users(output: str) -> List[Entry]:
    """Collapse `username ...` lines into one entry per account.

    EOS puts an account's ssh-key on its own `username x ssh-key ...` line, so
    lines are merged by name rather than taken one for one. The hash itself is
    never carried into the entry -- `no username x` is all that is needed to
    remove an account, and the report shows the type without the material.
    """
    accounts: Dict[str, Dict[str, Any]] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line.startswith("username "):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue

        name = tokens[1]
        info = accounts.setdefault(
            name, {"privilege": None, "role": None, "secret": None, "ssh_key": False}
        )
        rest = tokens[2:]
        index = 0
        while index < len(rest):
            token = rest[index]
            if token in ("privilege", "role") and index + 1 < len(rest):
                info[token] = rest[index + 1]
                index += 2
            elif token in ("secret", "password"):
                following = rest[index + 1] if index + 1 < len(rest) else None
                kind = following if following in SECRET_TYPES else "0"
                info["secret"] = f"{token} {kind}"
                break  # the rest of the line is hash material
            elif token == "nopassword":
                info["secret"] = "nopassword"
                index += 1
            elif token == "ssh-key":
                info["ssh_key"] = True
                break
            else:
                index += 1

    entries = []
    for name, info in accounts.items():
        entries.append(
            Entry(
                key=name,
                # `no username x` removes the account whatever its options are,
                # and keeps the hash out of the command we build.
                line=f"username {name}",
                display=_describe(name, info),
                # `username x` alone would create the account with no password.
                data={**info, "restorable": False},
            )
        )
    return entries


def _ssh_key_entry(account: Entry) -> Entry:
    """The negation that strips an account's ssh-key.

    Emitted before the account's own negation, while the account still exists,
    so the key is gone whether or not `no username x` takes it along. IOS has
    no such command -- and no such line to parse -- so this only ever fires for
    an account whose config actually showed one.
    """
    return Entry(key=account.key, line=f"username {account.key} ssh-key")


def _describe(name: str, info: Mapping[str, Any]) -> str:
    parts = [f"username {name}"]
    if info["privilege"]:
        parts.append(f"privilege {info['privilege']}")
    if info["role"]:
        parts.append(f"role {info['role']}")
    if info["secret"]:
        parts.append(info["secret"])
        if info["secret"] in WEAK_TYPES:
            parts.append("(weak)")
    if info["ssh_key"]:
        parts.append("+ ssh-key")
    return " ".join(parts)


def plan_users(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    """Negate every managed account that exists, then write them all back.

    Comparison is case sensitive: `Admin` and `admin` are different accounts on
    both platforms.
    """
    context = context or {}
    variables = context.get("variables") or {}
    only_missing = bool(variables.get("only_missing"))
    allow_remove_self = bool(variables.get("allow_remove_self"))
    login_user = context.get("login_user")

    existing = {entry.key: entry for entry in current}
    managed = list(desired)

    to_add: List[str] = []
    to_remove: List[Entry] = []
    for name in managed:
        found = existing.get(name)
        if found is None:
            to_add.append(name)
            continue
        if found.data.get("ssh_key"):
            # Strip the key even under --only-missing: leaving an alternative
            # credential on an account whose password we manage defeats the
            # point of managing it.
            to_remove.append(_ssh_key_entry(found))
        if only_missing:
            continue  # the account is there, so its password is left alone
        to_remove.append(found)  # negate first; the template orders it
        to_add.append(name)

    if mode == MODE_REPLACE:
        for name, entry in existing.items():
            if name in managed:
                continue
            if name == login_user and not allow_remove_self:
                # Purging the account this session is authenticated with is the
                # one mistake there is no recovering from remotely.
                continue
            if entry.data.get("ssh_key"):
                to_remove.append(_ssh_key_entry(entry))
            to_remove.append(entry)

    return to_add, to_remove


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #


def password_variable(name: str) -> str:
    """Environment variable holding this account's password."""
    return "NETOPS_PW_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _looks_hashed(value: str) -> bool:
    return bool(re.match(r"^\$[0-9a-z]{1,6}\$", value))


def resolve_passwords(names: Sequence[str], args: argparse.Namespace) -> Dict[str, str]:
    """One password per managed account, from AWS, the environment, or a prompt."""
    from ..credentials import CredentialError, fetch_json_secret

    mapping: Dict[str, str] = {}
    if args.password_secret:
        document = fetch_json_secret(args.password_secret, args.aws_region)
        mapping = {str(k): str(v) for k, v in document.items() if v is not None}

    resolved: Dict[str, str] = {}
    for name in names:
        variable = password_variable(name)
        value = mapping.get(name) or os.environ.get(variable)
        if not value and sys.stdin.isatty():
            value = _prompt(name)
        if not value:
            raise CredentialError(
                f"no password for {name!r}: put it in ${variable}, in the "
                f"--password-secret JSON, or run interactively"
            )
        resolved[name] = _validate_password(name, value, args.hash_type)
    return resolved


def _prompt(name: str) -> str:
    import getpass

    first = getpass.getpass(f"New password for {name}: ")
    if not first:
        return ""
    if first != getpass.getpass(f"Confirm password for {name}: "):
        raise ValueError(f"passwords for {name} did not match")
    return first


def _validate_password(name: str, value: str, hash_type: Optional[str]) -> str:
    validate_secret_value(value, f"password for {name}")
    if hash_type:
        return value  # a pre-computed hash: length rules do not apply
    if _looks_hashed(value):
        raise ValueError(
            f"the password for {name} looks like a {value.split('$')[1]}-type hash; "
            f"pass --hash-type (9 or 8 or 5 on IOS, sha512 or 5 on EOS) to send it "
            f"as one, since the platforms spell the keyword differently"
        )
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"the password for {name} is shorter than {MIN_PASSWORD_LENGTH} characters"
        )
    return value


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-U",
        "--user",
        action="append",
        required=True,
        metavar="NAME[,NAME...]",
        help="local account(s) to manage; repeatable and/or comma separated",
    )
    parser.add_argument(
        "--privilege",
        default="15",
        help="privilege level for the managed accounts",
    )
    parser.add_argument("--role", help="EOS role for the managed accounts, e.g. network-admin")
    parser.add_argument(
        "--algorithm",
        choices=("md5", "sha256", "scrypt"),
        help="IOS algorithm-type for a plaintext password (scrypt is type 9); "
        "omit to let the device use its default",
    )
    parser.add_argument(
        "--hash-type",
        metavar="TYPE",
        help="treat the supplied value as a pre-computed hash of this type "
        "(IOS: 5, 8, 9 -- EOS: 5, sha512) instead of a plaintext password",
    )
    parser.add_argument(
        "--password-secret",
        metavar="NAME_OR_ARN",
        default=os.environ.get("NETOPS_PW_SECRET"),
        help="AWS secret holding a JSON object of {username: password} "
        "[$NETOPS_PW_SECRET]",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="create accounts that are absent, but leave existing ones alone "
        "(onboarding without rotating); an ssh-key on a managed account is "
        "still removed",
    )
    parser.add_argument(
        "--allow-remove-self",
        action="store_true",
        help="with --replace, also purge the account this run is logged in as",
    )


def build_desired(args: argparse.Namespace) -> Desired:
    names: List[str] = []
    for chunk in args.user:
        for value in chunk.split(","):
            if not value.strip():
                continue
            name = validate_word(value, "username")
            if name not in names:
                names.append(name)
    if not names:
        raise ValueError("--user did not name any accounts")

    passwords = resolve_passwords(names, args)

    return Desired(
        keys=names,
        variables={
            "users": passwords,
            "privilege": validate_word(args.privilege, "privilege") if args.privilege else None,
            "role": validate_word(args.role, "role") if args.role else None,
            "algorithm": args.algorithm,
            "hash_type": validate_word(args.hash_type, "hash type") if args.hash_type else None,
            "only_missing": args.only_missing,
            "allow_remove_self": args.allow_remove_self,
        },
        secrets=list(passwords.values()),
    )


FEATURE = Feature(
    name="users",
    help="set local accounts and rotate their passwords",
    platforms={
        "cisco_ios": PlatformSupport(SHOW_COMMAND, parse_users, IOS_SAMPLE),
        "arista_eos": PlatformSupport(SHOW_COMMAND, parse_users, EOS_SAMPLE),
    },
    add_arguments=add_arguments,
    build_desired=build_desired,
    plan=plan_users,
    # Undoing a rotation would mean knowing the password it replaced. The
    # device stores a hash, so nothing can. Rolling back the *negation* alone
    # would leave the account gone, which is worse than either state.
    reversible=False,
    rollback_note=(
        "a local account's password is stored hashed, so the previous one cannot "
        "be read back and a rotation cannot be undone. Take a backup first if "
        "you may need to reverse this."
    ),
    selftest_args=["--user", "admin,netauto", "--privilege", "15"],
    selftest_env={
        "NETOPS_PW_ADMIN": "selftest-placeholder",
        "NETOPS_PW_NETAUTO": "selftest-placeholder",
    },
)
