"""NTP servers, and the authentication keys that go with them.

The parser keeps the device's own line for removal and rollback. Comparison
includes the server, VRF, key, source, preference and (on EOS) iburst. Correcting
an endpoint resets its old line first while preserving unmanaged options.

Authentication is three lines plus a binding: the key itself, a trusted-key
entry, `ntp authenticate`, and `key <id>` on each server. They are ordered so
the fleet never authenticates against a key it does not have yet -- the key and
the trusted-key first, then the servers that reference it, and `ntp
authenticate` last of all.

The key material is a secret. It comes from the environment or AWS, is scrubbed
out of everything that gets printed, and cannot be read back off a device: IOS
stores it type-7 encrypted, so a changed key is invisible. `--rewrite-keys`
re-issues it.
"""

from __future__ import annotations

import argparse
import json
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
    validate_word,
)
from ..netbox import source_for
from ..ntp_discovery import global_sources, interface_name
from .. import aruba_ntp, clearpass_ntp, f5_ntp, junos_ntp, ntp_nxos
from .waf import add_connection_arguments, connection_settings
from ..standards import StandardsError, host_and_port, of as standards_of

# Wide enough to see the authentication lines, narrow enough that `ntp master`,
# `ntp access-group` and global source settings are never managed entries, so
# cannot be removed by --replace. Global sources are read for comparison only.
SHOW_COMMAND = "show running-config | include ^ntp"

#: Shortest key this tool will set.
MIN_KEY_LENGTH = 8

IOS_SAMPLE = """\
ntp authentication-key 1 md5 072C285F4D06 7
ntp authenticate
ntp trusted-key 1
ntp source Loopback0
ntp server 10.10.10.1 key 1
ntp server 10.10.10.2 prefer
ntp server vrf MGMT 192.168.5.5 source GigabitEthernet0/0
"""

EOS_SAMPLE = """\
ntp authentication-key 1 md5 7 072C285F4D06
ntp trusted-key 1
ntp server 10.10.10.1 key 1 iburst
ntp server vrf MGMT 192.168.5.5 prefer iburst
ntp server time.example.net
"""


def _server_key(host: str, key_id: Optional[str], source: Optional[str] = None) -> str:
    """The comparison key for a server, including how it is configured.

    The auth key and the source interface are both part of what makes a server
    line correct, so a server configured without its key -- or sourced from the
    wrong interface -- differs from one configured properly. Without that, a
    stale `source Loopback0` on a device that should no longer have one would
    read as compliant forever.
    """
    base = f"server:{normalize(host)}"
    if key_id:
        base += f":key:{key_id}"
    if source:
        base += f":source:{source}"
    return base


def parse_ntp(output: str) -> List[Entry]:
    """Pull servers and authentication out of `ntp ...` lines.

    Shared by IOS and EOS. Both render a server as
    ``ntp server [vrf NAME] <host> [options...]``, and both spell the
    authentication lines the same way, though they order the key's own
    arguments differently.
    """
    entries: List[Entry] = []
    sources = global_sources(output)
    for raw in output.splitlines():
        line = raw.strip()
        if not line.startswith("ntp "):
            continue
        tokens = line.split()
        if len(tokens) < 2:
            continue
        kind = tokens[1]

        if kind == "server":
            index = 2
            vrf = None
            if len(tokens) > index and tokens[index] == "vrf":
                vrf = tokens[index + 1] if len(tokens) > index + 1 else None
                index += 2  # skip 'vrf' '<name>'
            if index >= len(tokens):
                raise ValueError('Incomplete NTP server configuration')
            host = tokens[index]
            options = {}
            extra = []
            position = index + 1
            while position < len(tokens):
                keyword = tokens[position]
                if keyword in ('key', 'source'):
                    if position + 1 >= len(tokens):
                        raise ValueError(f'Incomplete NTP server {keyword} option')
                    value = tokens[position + 1]
                    position += 2
                    if keyword == 'source' and value.isalpha() and position < len(tokens) and tokens[position].isdigit():
                        value += tokens[position]
                        position += 1
                    options[keyword] = value
                elif keyword in ('prefer', 'iburst'):
                    position += 1
                else:
                    extra.append(keyword)
                    position += 1
            entries.append(
                Entry(
                    key=_server_key(host, options.get("key"), options.get("source")),
                    line=line,
                    data={
                        "kind": "server",
                        "host": normalize(host),
                        "key": options.get("key"),
                        "source": options.get("source"),
                        "inherited_source": sources.get(vrf or 'default') or sources.get('*'),
                        "vrf": vrf,
                        "prefer": "prefer" in tokens[index + 1:],
                        "iburst": "iburst" in tokens[index + 1:],
                        "extra": extra,
                    },
                )
            )
        elif kind == "authentication-key" and len(tokens) >= 4:
            # IOS: `ntp authentication-key 1 md5 <cipher> 7`
            # EOS: `ntp authentication-key 1 md5 7 <cipher>`
            # Either way the id and the algorithm are the first two arguments,
            # and the material is unreadable -- so only those are compared.
            entries.append(
                Entry(
                    key=f"key:{tokens[2]}:{tokens[3]}",
                    line=f"ntp authentication-key {tokens[2]}",
                    display=f"ntp authentication-key {tokens[2]} {tokens[3]} <hidden>",
                    data={
                        "kind": "key",
                        "id": tokens[2],
                        "type": tokens[3],
                        # `ntp authentication-key 1` on its own is not a command,
                        # and the material it needs is stored encrypted.
                        "restorable": False,
                    },
                )
            )
        elif kind == "trusted-key" and len(tokens) >= 3:
            for key_id in tokens[2:]:
                entries.append(
                    Entry(
                        key=f"trusted-key:{key_id}",
                        line=f"ntp trusted-key {key_id}",
                        data={"kind": "trusted-key", "id": key_id},
                    )
                )
        elif kind == "authenticate":
            entries.append(
                Entry(key="authenticate", line="ntp authenticate", data={"kind": "authenticate"})
            )
    return entries


def plan_ntp(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    context = context or {}
    if context.get('platform') == 'cisco_nxos' and not context.get('nxos_servers'):
        return ntp_nxos.plan(current, desired, mode, {**context, 'nxos_servers': True})
    variables = context.get("variables") or {}
    configured = {entry.key for entry in current}
    entries = variables.get("entries") or {}
    eos = context.get("platform") == "arista_eos"

    def matches(entry, wanted):
        source = entry.data.get('source')
        if wanted.get('source'):
            source = source or entry.data.get('inherited_source')
            try:
                source_matches = interface_name(source or '').lower() == interface_name(wanted['source']).lower()
            except ValueError:
                source_matches = source == wanted['source']
        else:
            source_matches = not source
        return (entry.data.get('kind') == 'server' and entry.data.get('host') == wanted['host']
                and entry.data.get('key') == wanted.get('key') and source_matches
                and entry.data.get("vrf") == variables.get("vrf")
                and bool(entry.data.get("prefer")) == (wanted["host"] == variables.get("prefer"))
                and (not eos or bool(entry.data.get("iburst")) == bool(variables.get("iburst"))))
    if variables.get("regions") and isinstance(context.get("notes"), list):
        # Say which set this device was measured against; with regions it differs per device.
        region = variables.get("region")
        context["notes"].append(f"NTP servers for region {region}" if region else "NTP servers: default set (no region matched)")

    to_add = [key for key in desired if key not in configured]
    for key in desired:
        wanted = entries.get(key, {})
        if wanted.get("kind") == "server":
            if any(matches(entry, wanted) for entry in current):
                if key in to_add:
                    to_add.remove(key)
            elif key not in to_add:
                to_add.append(key)
    if variables.get("rewrite_keys") and not context.get("verification"):
        # The material is stored encrypted and cannot be compared, so this is
        # the only way to push a changed one.
        to_add.extend(
            key
            for key in desired
            if key.startswith("key:") and key not in to_add
        )

    to_add = [key for key in desired if key in to_add]
    # Reset an existing endpoint before re-adding it: omitting a CLI option
    # does not reliably clear a previously configured source/prefer/key.
    to_remove: List[Entry] = []
    for entry in current:
        if entry.data.get("kind") != "server":
            continue
        for key in to_add:
            wanted = entries.get(key, {})
            if (wanted.get("kind") == "server" and entry.data.get("host") == wanted["host"]
                    and entry.data.get("vrf") == variables.get("vrf")):
                to_remove.append(Entry(key=entry.key, line=entry.line,
                                       data={**entry.data, "before_add": True}))
                # Retain options outside this standard, such as version/minpoll.
                extra = list(entry.data.get('extra', []))
                if not eos and entry.data.get('iburst'):
                    extra.append('iburst')
                variables.setdefault('server_options', {})[key] = extra
                break
    if mode == MODE_REPLACE:
        wanted = set(desired)
        # Silence is not a statement. A standards file that says nothing about
        # authentication is not asking for it to be torn off devices that have
        # it -- only a file that declares `authentication:` manages those lines.
        manages_auth = bool(variables.get("manages_auth"))
        wanted_hosts = {(entry["host"], variables.get("vrf")) for entry in entries.values()
                        if entry.get("kind") == "server"}
        wanted_key_ids = {entry["id"] for entry in entries.values() if entry.get("kind") == "key"}
        for entry in current:
            if entry.data.get("kind") == "server":
                if (entry.data.get("host"), entry.data.get("vrf")) in wanted_hosts:
                    continue
            elif entry.key in wanted:
                continue
            if not manages_auth and entry.data.get("kind") != "server":
                continue
            if entry.data.get("kind") == "key" and entry.data.get("id") in wanted_key_ids:
                # Replacing the algorithm at the same ID must not delete the new key.
                continue
            if eos and entry.data.get('kind') == 'server' and entry.data.get('vrf') != variables.get('vrf'):
                # EOS permits NTP in only one VRF; clear old-VRF servers first.
                entry = Entry(key=entry.key, line=entry.line, data={**entry.data, 'before_add': True})
            to_remove.append(entry)
    return to_add, to_remove


# --------------------------------------------------------------------------- #
# key material
# --------------------------------------------------------------------------- #


def key_variable(key_id: str) -> str:
    return "NETOPS_NTP_KEY_" + re.sub(r"[^A-Za-z0-9]", "_", str(key_id)).upper()


def resolve_key(key_id: str, args: argparse.Namespace) -> str:
    """The key material, from AWS or the environment or a prompt."""
    from ..credentials import CredentialError, fetch_json_secret

    value = None
    if getattr(args, "key_secret", None):
        document = fetch_json_secret(args.key_secret, args.aws_region)
        value = document.get(str(key_id))
    variable = key_variable(key_id)
    value = value or os.environ.get(variable)
    if not value and sys.stdin.isatty():
        import getpass

        value = getpass.getpass(f"NTP key {key_id}: ")
    if not value:
        raise CredentialError(
            f"no material for NTP key {key_id}: put it in ${variable}, in the "
            f"--key-secret JSON, or run interactively"
        )
    validate_secret_value(value, f"NTP key {key_id}")
    if len(value) < MIN_KEY_LENGTH:
        raise ValueError(f"the NTP key {key_id} is shorter than {MIN_KEY_LENGTH} characters")
    return value


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def _word(value):
    return validate_address(str(value)) if value else None


def add_arguments(parser: argparse.ArgumentParser) -> None:
    add_connection_arguments(parser)
    parser.add_argument('--allow-clearpass-cluster-changes', action='store_true',
                        help='explicitly authorize NTP changes to the global NetBox-tagged ClearPass cluster')
    parser.add_argument('--clearpass-cluster-snapshot', help=argparse.SUPPRESS)
    parser.add_argument('--mobility-conductor-snapshot', help=argparse.SUPPRESS)
    parser.add_argument(
        "-s",
        "--servers",
        action="append",
        metavar="ADDR[,ADDR...]",
        help="desired NTP server(s); repeatable and/or comma separated. "
        "Defaults to ntp.servers in the standards file.",
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
        default=None,
        help="omit iburst on platforms whose template uses it (Arista)",
    )
    parser.add_argument(
        "--key-secret",
        metavar="NAME_OR_ARN",
        default=os.environ.get("NETOPS_NTP_SECRET"),
        help='AWS secret holding {"<key id>": "<material>"} [$NETOPS_NTP_SECRET]',
    )
    parser.add_argument(
        "--rewrite-keys",
        action="store_true",
        help="re-issue the authentication key even though it looks present. "
        "The material is stored encrypted and cannot be compared, so this is "
        "the only way to push a changed one.",
    )


def build_desired(args: argparse.Namespace) -> Desired:
    standards = standards_of(args)
    conductor_snapshot = None
    if getattr(args, 'mobility_conductor_snapshot', None):
        from ..mobility_conductor import validate
        with open(args.mobility_conductor_snapshot, encoding='utf-8') as stream:
            conductor_snapshot = validate(json.load(stream))
    cluster_snapshot = None
    if getattr(args, 'clearpass_cluster_snapshot', None):
        from ..clearpass_cluster import validate
        with open(args.clearpass_cluster_snapshot, encoding='utf-8') as stream:
            cluster_snapshot = validate(json.load(stream))

    # A flag beats the file, so a one-off run never needs the file edited --
    # and it beats the regional sets too: --servers means these, everywhere.
    regions: Dict[str, Dict[str, Any]] = {}
    if args.servers:
        wanted = [chunk for value in args.servers for chunk in value.split(",")]
        servers, prefer = server_set(wanted, args.prefer, "--servers")
    else:
        wanted, flagged = _listed(standards.entries("ntp.servers"), None)
        servers, prefer = server_set(wanted, args.prefer or flagged or standards.value("ntp.prefer"),
                                     "ntp.servers", allow_empty=True)
        regions = regional_sets(standards)
    if not servers and not regions:
        raise ValueError(
            "no NTP servers given: pass --servers or set ntp.servers (or ntp.regions) in the standards file"
        )

    # --- authentication -----------------------------------------------------
    auth = standards.section("ntp").get("authentication") or {}
    if auth and not isinstance(auth, Mapping):
        raise StandardsError("ntp.authentication must be a mapping")

    key_id: Optional[str] = None
    secrets: List[str] = []
    entries: Dict[str, Dict[str, Any]] = {}
    keys: List[str] = []

    if auth:
        if not auth.get("key_id"):
            raise StandardsError("ntp.authentication needs a key_id")
        key_id = validate_word(str(auth["key_id"]), "NTP key id")
        key_type = validate_word(str(auth.get("type", "md5")), "NTP key type")
        material = resolve_key(key_id, args)
        secrets.append(material)

        # The key exists before anything references it...
        key = f"key:{key_id}:{key_type}"
        keys.append(key)
        entries[key] = {
            "kind": "key",
            "id": key_id,
            "type": key_type,
            "material": material,
        }
        if auth.get("trusted", True):
            trusted = f"trusted-key:{key_id}"
            keys.append(trusted)
            entries[trusted] = {"kind": "trusted-key", "id": key_id}

    source = args.source or standards.value("ntp.source")
    source = interface_name(str(source)) if source else None
    iburst = standards.value("ntp.iburst", True) if args.iburst is None else args.iburst
    if type(iburst) is not bool:
        raise StandardsError("ntp.iburst must be true or false")
    for host in servers:
        key = _server_key(host, key_id, source)
        keys.append(key)
        entries[key] = {"kind": "server", "host": host, "key": key_id, "source": source}

    # ...and authentication is switched on last, once every server can satisfy it.
    if auth and auth.get("enable", True):
        keys.append("authenticate")
        entries["authenticate"] = {"kind": "authenticate"}

    return Desired(
        keys=keys,
        variables={
            "entries": entries,
            "servers": servers,
            "regions": regions,
            "key_id": key_id,
            "vrf": _word(args.vrf or standards.value("ntp.vrf")),
            "prefer": prefer,
            "source": source,
            "iburst": iburst,
            "manages_auth": bool(auth),
            "rewrite_keys": bool(getattr(args, "rewrite_keys", False)),
            "f5": connection_settings(args),
            "aruba": aruba_ntp.settings(standards.value("ntp.aruba")),
            "clearpass": clearpass_ntp.settings(standards.value("ntp.clearpass")),
            "allow_clearpass_cluster_changes": bool(getattr(args, 'allow_clearpass_cluster_changes', False)),
            "clearpass_cluster_snapshot": cluster_snapshot,
            "mobility_conductor_snapshot": conductor_snapshot,
        },
        secrets=secrets,
    )


def _listed(items, preferred):
    """Hosts and the preferred one from a list of servers as the standards file writes them."""
    wanted = []
    for item in items:
        record = host_and_port(item)
        wanted.append(record["host"])
        if record.get("prefer") and not preferred:
            preferred = record["host"]
    return wanted, preferred


def server_set(wanted, preferred, where, allow_empty=False):
    """Validated, de-duplicated servers in order, and the preferred one if any."""
    servers: List[str] = []
    for value in wanted:
        if not str(value).strip():
            continue
        host = normalize(validate_address(str(value)))
        if host not in servers:
            servers.append(host)
    if not servers and not allow_empty:
        raise ValueError(f"{where}: no NTP servers listed")
    prefer = normalize(validate_address(str(preferred))) if preferred else None
    if prefer and prefer not in servers:
        raise ValueError(f"{where}: preferred server {preferred} is not one of the desired servers")
    return servers, prefer


def regional_sets(standards) -> Dict[str, Dict[str, Any]]:
    """`ntp.regions`: NetBox region slug -> its own servers, checked before any device is dialled.

        ntp:
          servers: [10.50.0.10]            # everyone no region below matches
          regions:
            us-east: [10.1.0.10, 10.1.0.11]
            emea:
              servers: [10.2.0.10, 10.2.0.11]
              prefer: 10.2.0.10
    """
    section = standards.section("ntp").get("regions")
    if section is None:
        return {}
    if not isinstance(section, Mapping):
        raise StandardsError("ntp.regions must map region slugs to server lists")
    regions = {}
    for slug, value in section.items():
        where = f"ntp.regions.{slug}"
        if isinstance(value, Mapping):
            unknown = set(value) - {"servers", "prefer"}
            if unknown:
                raise StandardsError(f"{where}: unknown keys {sorted(unknown)}; use servers and prefer")
            items, preferred = value.get("servers") or [], value.get("prefer")
        else:
            items, preferred = value, None
        if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
            raise StandardsError(f"{where} must be a list of servers")
        servers, prefer = server_set(*_listed([standards.resolve(item) for item in items], preferred), where)
        regions[str(slug).strip().lower()] = {"servers": servers, "prefer": prefer, "region": str(slug)}
    return regions


def region_for(host, regions):
    """The nearest of the device's regions that has its own servers, or None."""
    data = getattr(host, "data", {}) or {}
    chain = data.get("regions")
    if not chain:
        # A CSV can carry a `region` column; NetBox inventory supplies the whole chain.
        chain = [data["region"]] if data.get("region") else []
    for slug in chain:
        found = regions.get(str(slug).strip().lower())
        if found:
            return found
    return None


def per_device(keys, variables, host):
    """This device's servers (its region's, if it has one) and source interface."""
    servers, prefer = variables.get("servers", []), variables.get("prefer")
    regions = variables.get("regions") or {}
    regional = region_for(host, regions) if regions else None
    if regional:
        servers, prefer = regional["servers"], regional["prefer"]
    elif not servers:
        where = (getattr(host, "data", {}) or {}).get("region") or "no region"
        raise ValueError(f"no NTP servers for this device ({where}): add its region under ntp.regions "
                         "or set ntp.servers as the default")

    source, authoritative = source_for(host, "ntp")
    if authoritative:
        source = interface_name(str(source)) if source else None
    else:
        source = variables.get("source")  # a CSV has no opinion; the file's value stands
    vrf = variables.get('vrf')
    selected_vrf = (getattr(host, 'data', {}) or {}).get('ntp_vrf')
    if selected_vrf:
        vrf = None if selected_vrf == 'default' else validate_word(str(selected_vrf), 'NTP VRF')
    if (servers == variables.get("servers") and source == variables.get("source") and vrf == variables.get('vrf')
            and prefer == variables.get("prefer") and not regional):
        return keys, variables

    entries = {key: record for key, record in variables["entries"].items() if record["kind"] != "server"}
    server_keys = []
    for server in servers:
        key = _server_key(server, variables.get("key_id"), source)
        server_keys.append(key)
        entries[key] = {"kind": "server", "host": server, "key": variables.get("key_id"), "source": source}
    # Servers sit after the key and trusted-key, and before `ntp authenticate`.
    others = [key for key in keys if variables["entries"][key]["kind"] != "server"]
    cut = others.index("authenticate") if "authenticate" in others else len(others)
    rebuilt = others[:cut] + server_keys + others[cut:]
    changed = {"entries": entries, "source": source, "servers": servers, "prefer": prefer, "vrf": vrf}
    if regional:
        changed["region"] = regional["region"]
    return rebuilt, {**variables, **changed}


FEATURE = Feature(
    name="ntp",
    platform_runs={'f5_tmsh': f5_ntp.run, 'aruba_os': aruba_ntp.run,
                   'aruba_clearpass': clearpass_ntp.run, 'juniper_junos': junos_ntp.run},
    help="converge the NTP servers and their authentication key",
    platforms={
        "cisco_ios": PlatformSupport(SHOW_COMMAND, parse_ntp, IOS_SAMPLE),
        "arista_eos": PlatformSupport(SHOW_COMMAND, parse_ntp, EOS_SAMPLE),
        "cisco_nxos": PlatformSupport('show running-config ntp', ntp_nxos.parse, ntp_nxos.SAMPLE),
    },
    add_arguments=add_arguments,
    build_desired=build_desired,
    plan=plan_ntp,
    verify_with_plan=True,
    per_device=per_device,
    rollback_note=(
        "an authentication key's material is stored encrypted, so a changed key "
        "cannot be put back; the servers and the trusted-key can"
    ),
    # 10.10.10.1 is already in both samples, so the selftest also shows that a
    # server already configured produces no command.
    selftest_args=[
        "--servers",
        "10.10.10.1,10.99.99.1,10.99.99.2",
        "--prefer",
        "10.99.99.1",
    ],
    selftest_env={"NETOPS_NTP_KEY_1": "selftest-placeholder"},
)
