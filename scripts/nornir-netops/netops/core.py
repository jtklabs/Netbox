"""Platform-neutral plumbing shared by every feature.

A *feature* (ntp today, syslog tomorrow) only has to know three things:

* which show command reveals its current state on each platform,
* how to turn that output into comparable entries,
* which CLI arguments describe the desired state.

Everything else -- diffing, template rendering, add vs replace -- lives here, so
adding a feature means dropping a module in ``netops/features/`` plus a template
in ``templates/<platform>/<feature>.j2``.

Nothing in this module imports nornir or netmiko. That keeps ``selftest`` (and
template development generally) runnable on a laptop with only Jinja2 present.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def template_dir() -> Path:
    """Where templates/<platform>/<feature>.j2 lives.

    Normally the `templates/` directory beside this package -- the checkout is
    the intended way to run and edit this. $NETOPS_TEMPLATES overrides it, which
    is also the escape hatch for a non-editable install, where only the package
    itself is copied.
    """
    override = os.environ.get("NETOPS_TEMPLATES")
    if override:
        return Path(override).expanduser().resolve()
    package = Path(__file__).resolve().parent
    for candidate in (package.parent / "templates", package / "templates"):
        if candidate.is_dir():
            return candidate
    return package.parent / "templates"  # let Jinja report the missing template


@lru_cache(maxsize=None)
def _environment(directory: str) -> Environment:
    """One Jinja environment per template directory.

    StrictUndefined: a typo in a template is a loud error, not a silently
    missing keyword on a config line we are about to push to production.
    """
    environment = Environment(
        loader=FileSystemLoader(directory),
        undefined=StrictUndefined,
        keep_trailing_newline=False,
    )
    # Address arithmetic a template should not be doing by hand. The template
    # still decides the shape of the command; this only reformats the address.
    environment.filters["wildcard"] = wildcard
    environment.filters["netmask"] = netmask
    return environment


def wildcard(network: str) -> str:
    """CIDR as IOS writes it: `10.1.1.0/24` -> `10.1.1.0 0.0.0.255`,
    and a single host as `host 10.1.1.5`."""
    parsed = ipaddress.ip_network(str(network).strip(), strict=False)
    if parsed.prefixlen == parsed.max_prefixlen:
        return f"host {parsed.network_address}"
    bits = int(parsed.netmask) ^ ((1 << parsed.max_prefixlen) - 1)
    inverted = ipaddress.ip_address(bits)
    return f"{parsed.network_address} {inverted}"


def netmask(network: str) -> str:
    """CIDR as a dotted mask: `10.1.1.0/24` -> `10.1.1.0 255.255.255.0`."""
    parsed = ipaddress.ip_network(str(network).strip(), strict=False)
    return f"{parsed.network_address} {parsed.netmask}"

# Spellings we accept in the CSV / autodetect, mapped to the netmiko platform
# name used for both the connection and the template directory.
PLATFORM_ALIASES = {
    "ios": "cisco_ios",
    "iosxe": "cisco_ios",
    "ios-xe": "cisco_ios",
    "ios_xe": "cisco_ios",
    "cisco_xe": "cisco_ios",
    "cisco_ios_xe": "cisco_ios",
    "cisco_iosxe": "cisco_ios",
    "cisco_ios_telnet": "cisco_ios",
    "eos": "arista_eos",
    "arista": "arista_eos",
    "arista_eos_telnet": "arista_eos",
}

# How each platform is told to persist the running config. Kept explicit rather
# than delegating to netmiko's save_config() so the dry run can print the exact
# command that an apply would run.
SAVE_COMMANDS = {
    "cisco_ios": "write memory",
    "arista_eos": "write memory",
}

MODE_ADD = "add"
MODE_REPLACE = "replace"

# Everything a hostname or IP literal can legitimately contain. Desired values
# are interpolated straight into config lines, so anything else is rejected
# before it can smuggle a second command onto the device.
_SAFE_ADDRESS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

# A dotted quad that may carry leading zeros. Python's ipaddress rejects those
# outright (they are ambiguously octal), but a device will happily accept
# `010.1.1.1` and echo it back as `10.1.1.1` -- which would make every run think
# the server is missing and push it again.
_PADDED_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

# A bare word: username, role, vrf, algorithm name.
_SAFE_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")


class InvalidValue(ValueError):
    """A user-supplied value is not safe or sensible to put in a config line."""


def validate_address(value: str) -> str:
    """Return `value` if it is a plausible IP or hostname, else raise."""
    text = value.strip()
    if not _SAFE_ADDRESS.match(text) or len(text) > 253:
        raise InvalidValue(f"{value!r} is not a valid IP address or hostname")
    return text


def validate_text(value: str, kind: str = "text") -> str:
    """Free text destined for a config line: a location, a contact.

    Spaces are fine -- `snmp-server location ATL DC1 row 4` is one command --
    but a newline would end the command and start another one.
    """
    text = str(value).strip()
    if not text:
        raise InvalidValue(f"empty {kind}")
    if not text.isprintable():
        raise InvalidValue(f"{kind} may not contain newlines or control characters")
    if len(text) > 255:
        raise InvalidValue(f"{kind} is too long ({len(text)} characters)")
    return text


def validate_word(value: str, kind: str = "name") -> str:
    """Return `value` if it is a plausible bare word (username, role, vrf)."""
    text = value.strip()
    if not _SAFE_WORD.match(text) or len(text) > 64:
        raise InvalidValue(f"{value!r} is not a valid {kind}")
    return text


def validate_secret_value(value: str, kind: str = "password") -> str:
    """Return `value` if it can safely be the last field of a config line.

    A password is never quoted on the wire, so whitespace or a control
    character would either truncate the command or append a second one.
    """
    if not value:
        raise InvalidValue(f"empty {kind}")
    if any(character.isspace() for character in value):
        raise InvalidValue(f"{kind} may not contain whitespace")
    if not value.isprintable():
        raise InvalidValue(f"{kind} may not contain control characters")
    return value


#: What a redacted secret looks like everywhere a human or a report can see it.
REDACTED = "<redacted>"


def scrub(text: Optional[str], secrets: Sequence[str]) -> Optional[str]:
    """Replace every secret value with a placeholder.

    Applied to rendered commands, device output and the JSON report, so a
    password reaches the device and nowhere else. Deliberately unconditional:
    over-redacting a short password is the harmless direction to be wrong in.
    """
    if not text:
        return text
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def canonical_platform(platform: str | None) -> str:
    """Normalize a platform string ('IOS' -> 'cisco_ios')."""
    value = (platform or "").strip().lower()
    return PLATFORM_ALIASES.get(value, value)


def normalize(value: str) -> str:
    """Canonical form of a server address, so 010.1.1.1 == 10.1.1.1.

    Hostnames are lowercased. Anything that is not an address is returned as
    given, which lets a feature compare non-address entries (a syslog facility,
    say) with the same helper.
    """
    text = value.strip()
    if _PADDED_IPV4.match(text):
        text = ".".join(str(int(octet)) for octet in text.split("."))
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text.lower()


@dataclass(frozen=True)
class Entry:
    """One configured item found on a device.

    ``key`` is what we compare on (the server address, the username). ``line``
    is what gets negated to remove it -- usually the device's own config line,
    so options we do not model (``prefer``, ``source Vlan10``, a vrf) are
    removed with it instead of leaving a half-configured statement.

    ``display`` is what the report shows, for lines whose real text holds a
    password hash that should not be printed. It defaults to ``line``.
    """

    key: str
    line: str
    display: Optional[str] = None
    #: Whatever else the parser noticed and the planner needs -- an EOS
    #: account's ssh-key, say. Excluded from equality so Entry stays hashable.
    data: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def shown(self) -> str:
        return self.display if self.display is not None else self.line


#: A feature's planner. Given what the device has, what we want, the mode and a
#: context, return the keys to configure and the entries to negate.
#: `plan_changes` is the default.
#:
#: The context carries "login_user", "platform", "variables", "ignores", and
#: "advisories" -- a list a planner can append to when it finds drift it is
#: deliberately not going to fix. The device is then reported as needing
#: attention rather than as compliant, without any command being sent.
PlanFunc = Callable[
    [Sequence["Entry"], Sequence[str], str, Mapping[str, Any]],
    Tuple[List[str], List["Entry"]],
]


@dataclass(frozen=True)
class PlatformSupport:
    """What a feature needs to know about one platform."""

    show_command: str
    parse: Callable[[str], List[Entry]]
    #: Sample device output, used by `configure.py selftest` and as living
    #: documentation of what the parser expects.
    sample: str = ""
    #: Fields this platform has no way to express, and which must therefore
    #: never be compared -- EOS has no `access <acl>` on an SNMP group, so
    #: comparing it would rebuild the group on every run, forever.
    ignores: Tuple[str, ...] = ()
    #: Further commands whose output is appended before parsing. SNMPv3 users
    #: are not in the running config on IOS, so reading them needs `show snmp
    #: user` as well; the parser sees both and sorts out which is which.
    extra_commands: Tuple[str, ...] = ()

    @property
    def commands(self) -> Tuple[str, ...]:
        return (self.show_command,) + tuple(self.extra_commands)


@dataclass(frozen=True)
class Desired:
    """The desired state a CLI invocation asks for."""

    #: Values compared against the device, e.g. NTP server addresses.
    keys: List[str]
    #: Extra template variables (vrf, prefer, ...). Every template for the
    #: feature must be renderable from this dict alone.
    variables: Dict[str, Any] = field(default_factory=dict)
    #: Values that must never be printed, reported or logged -- passwords go
    #: here. They reach the device; everywhere else they are scrubbed.
    secrets: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Feature:
    """A configuration domain: ntp, syslog, snmp, ..."""

    name: str
    help: str
    platforms: Dict[str, PlatformSupport]
    #: Adds the feature's own CLI arguments to its subparser.
    add_arguments: Callable[[argparse.ArgumentParser], None]
    #: Turns parsed args into a Desired.
    build_desired: Callable[[argparse.Namespace], Desired]
    #: A representative CLI invocation, used by `configure.py selftest` to
    #: render this feature's templates without touching a device.
    selftest_args: List[str] = field(default_factory=list)
    #: Environment the selftest sets first, for a feature whose values come
    #: from the environment rather than from a flag (passwords).
    selftest_env: Dict[str, str] = field(default_factory=dict)
    #: The same, but derived from the standards file, so adding a user to the
    #: file does not also mean editing a placeholder into this module.
    selftest_env_from: Optional[Callable[[Any], Dict[str, str]]] = None
    #: Platforms that genuinely have no equivalent setting, mapped to why.
    #: These are skipped and reported, not failed -- a fleet-wide run of a
    #: Cisco-only knob should not turn every Arista red.
    not_applicable: Dict[str, str] = field(default_factory=dict)
    #: Verify by re-running this feature's own planner rather than checking
    #: that the desired keys are present. For a feature whose desired set comes
    #: from the device itself, the keys are not known in advance.
    verify_with_plan: bool = False
    #: Adjusts the desired state for one device, given its host object.
    #: Most standards are the same everywhere; a source interface is not -- one
    #: switch sources from Loopback0, another from Vlan10, a third from
    #: nothing -- and that answer lives in the inventory, per device. Raising
    #: from here fails that device and no other.
    per_device: Optional[
        Callable[[List[str], Dict[str, Any], Any], Tuple[List[str], Dict[str, Any]]]
    ] = None
    #: Whether a blank line in this feature's template is content (a banner
    #: body) rather than layout.
    keep_blank_lines: bool = False
    #: Extra keyword arguments for the config push. A banner needs
    #: cmd_verify=False: the device stops echoing a prompt between the
    #: delimiters, and netmiko would otherwise wait for one that never comes.
    config_options: Dict[str, Any] = field(default_factory=dict)
    #: How this feature turns current + desired into a change. default_factory,
    #: not a plain default: a bare function default on a dataclass is a class
    #: attribute, and would bind `self` as its first argument.
    plan: PlanFunc = field(default_factory=lambda: plan_changes)

    def support_for(self, platform: str) -> PlatformSupport:
        if platform in self.not_applicable:
            raise NotApplicable(self.not_applicable[platform])
        try:
            return self.platforms[platform]
        except KeyError:
            supported = ", ".join(sorted(self.platforms))
            raise UnsupportedPlatform(
                f"platform {platform!r} has no '{self.name}' support "
                f"(supported: {supported})"
            ) from None


class UnsupportedPlatform(Exception):
    """Raised for a platform we have no template/parser for -- we fail the host
    rather than guessing at its syntax."""


class NotApplicable(Exception):
    """The setting does not exist on this platform. Not an error: the device is
    reported as skipped, with this message as the reason."""


def plan_changes(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    """Return (keys to configure, entries to negate) for the requested mode.

    add     -- converge on 'the desired entries are present'
    replace -- converge on 'the desired entries, and only those, are present'

    This is the default planner and the one verification uses. A feature whose
    change is not a set difference -- a password rotation, a scalar setting --
    supplies its own via ``Feature.plan``. `context` is unused here.
    """
    current_keys = {normalize(e.key) for e in current}
    desired_keys = {normalize(d) for d in desired}

    to_add = [d for d in desired if normalize(d) not in current_keys]
    to_remove: List[Entry] = []
    if mode == MODE_REPLACE:
        # Dedupe on the raw line: a device can legitimately list the same server
        # twice with different options, and each line needs its own negation.
        seen = set()
        for entry in current:
            if normalize(entry.key) in desired_keys or entry.line in seen:
                continue
            seen.add(entry.line)
            to_remove.append(entry)
    return to_add, to_remove


def render(
    feature: str,
    platform: str,
    add: Sequence[str],
    remove: Sequence[Entry],
    variables: Mapping[str, Any],
    keep_blank: bool = False,
) -> List[str]:
    """Render templates/<platform>/<feature>.j2 into a list of CLI commands.

    Blank lines are dropped, so a template can be laid out readably -- a bare
    `{% for %}` on its own line costs nothing. `keep_blank` turns that off for
    the one case where an empty line is content rather than formatting: the
    body of a banner.
    """
    template = _environment(str(template_dir())).get_template(f"{platform}/{feature}.j2")
    text = template.render(add=list(add), remove=list(remove), **dict(variables))
    lines = [line.rstrip() for line in text.splitlines()]
    if keep_blank:
        # Still drop the blank lines the block tags leave at either end.
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        return lines
    return [line for line in lines if line]
