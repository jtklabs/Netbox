"""Load the poller's configuration and its SNMPv3 credential sets.

Credentials live in their own file, separate from everything else, for one
practical reason: the main config is the sort of thing an operator will paste
into a ticket when asking why a scan behaved oddly, and SNMPv3 passphrases
should not travel that way. The credentials file is also checked for mode 0600
and complains if it is readable by anyone else.

Credential sets are ordered. The scanner tries them in the order written and
stops at the first that authenticates, so put the one that covers most of the
fleet first.
"""

from __future__ import annotations

import configparser
import logging
import os
import stat
from dataclasses import dataclass, field

from .snmp import Credential
from .sync import SyncOptions

log = logging.getLogger(__name__)

CREDENTIAL_SECTION_PREFIX = "credential"


@dataclass
class NetBoxConfig:
    url: str = ""
    token: str = ""
    verify_ssl: bool = True
    timeout: int = 30


@dataclass
class SnmpConfig:
    timeout: int = 5
    retries: int = 1
    use_bulk: bool = True
    max_repetitions: int = 25
    workers: int = 8
    # Where to remember which devices cannot answer a full-size GETBULK.
    # Blank disables it, and the only cost of that is rediscovering the limit
    # — several timeouts on the affected devices — on every single run.
    bulk_state_file: str = "/var/lib/snmp-inventory/getbulk-limits.json"
    bulk_state_ttl_days: int = 7


@dataclass
class Config:
    netbox: NetBoxConfig = field(default_factory=NetBoxConfig)
    snmp: SnmpConfig = field(default_factory=SnmpConfig)
    sync: SyncOptions = field(default_factory=SyncOptions)
    poller_name: str = ""
    scan_tag: str = ""
    credentials: list[Credential] = field(default_factory=list)

    def validate(self) -> None:
        problems = []
        if not self.netbox.url:
            problems.append("netbox.url is not set")
        if not self.netbox.token:
            problems.append("netbox.token is not set (or export NETBOX_TOKEN)")
        if not self.poller_name:
            problems.append("poller.name is not set")
        if not self.credentials:
            problems.append("no SNMPv3 credential sets were loaded")
        if problems:
            raise ValueError("; ".join(problems))


def load_for_probe(config_path: str, credentials_path: str = "") -> Config:
    """Config for a probe, which needs credentials and nothing else.

    A probe never talks to NetBox, so it must not demand a URL or a token.
    That matters on a jump box you have SSH'd into to ask one device what it
    is — there may be no poller config there at all, just a credentials file.
    """
    import os

    if os.path.exists(config_path):
        config = load(config_path, credentials_path)
    elif credentials_path:
        config = Config()
        config.credentials = load_credentials(credentials_path)
    else:
        raise FileNotFoundError(
            "no config at %s — pass --credentials pointing at an SNMPv3 "
            "credentials file, which is all a probe needs" % config_path
        )
    if not config.credentials:
        raise ValueError("no SNMPv3 credential sets were loaded")
    return config


def _parser() -> configparser.ConfigParser:
    """A parser that treats values as literal text.

    interpolation=None because configparser's default reads `%` as the start of
    a substitution, so a passphrase like `Str0ng%Pass` raises
    InterpolationSyntaxError — a confusing crash a long way from the real
    cause. Nothing here wants substitution: every value is a literal, and a
    passphrase most of all.
    """
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # keep key case; net-snmp option names are case-sensitive
    return parser


def load(config_path: str, credentials_path: str = "") -> Config:
    """Read the main config, then the credentials file it points at."""
    parser = _parser()
    if not parser.read(config_path):
        raise FileNotFoundError(f"config file not found: {config_path}")

    config = Config()

    if parser.has_section("netbox"):
        section = parser["netbox"]
        config.netbox = NetBoxConfig(
            url=section.get("url", ""),
            # An env var beats the file so a poller can be handed its token by
            # systemd or a secrets agent without the token ever being on disk.
            token=os.environ.get("NETBOX_TOKEN", "") or section.get("token", ""),
            verify_ssl=section.getboolean("verify_ssl", True),
            timeout=section.getint("timeout", 30),
        )

    if parser.has_section("poller"):
        section = parser["poller"]
        config.poller_name = section.get("name", "")
        config.scan_tag = section.get("scan_tag", "")
        config.sync = SyncOptions(
            device_role=section.get("device_role", "network"),
            access_point_role=section.get("access_point_role", "wireless-ap"),
            device_status=section.get("device_status", "active"),
            sync_interfaces=section.getboolean("sync_interfaces", True),
            sync_ips=section.getboolean("sync_ips", True),
            sync_modules=section.getboolean("sync_modules", True),
            sync_access_points=section.getboolean("sync_access_points", True),
            set_primary_ip=section.getboolean("set_primary_ip", True),
            manage_software_version=section.getboolean("manage_software_version", True),
            move_devices_between_sites=section.getboolean("move_devices_between_sites", True),
            retain_replaced_hardware=section.getboolean("retain_replaced_hardware", True),
            retired_device_status=section.get("retired_device_status", "inventory"),
            sync_cables=section.getboolean("sync_cables", True),
            cable_neighbor_classes=_class_list(
                section.get("cable_neighbor_classes", "network")),
        )

    if parser.has_section("snmp"):
        section = parser["snmp"]
        config.snmp = SnmpConfig(
            timeout=section.getint("timeout", 5),
            retries=section.getint("retries", 1),
            use_bulk=section.getboolean("use_bulk", True),
            max_repetitions=section.getint("max_repetitions", 25),
            workers=section.getint("workers", 8),
            bulk_state_file=section.get(
                "bulk_state_file", "/var/lib/snmp-inventory/getbulk-limits.json"),
            bulk_state_ttl_days=section.getint("bulk_state_ttl_days", 7),
        )

    path = credentials_path or (
        parser.get("poller", "credentials_file", fallback="") if parser.has_section("poller") else ""
    )
    if path:
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(config_path)), path)
        config.credentials = load_credentials(path)
    else:
        # Credential sections may also live in the main file, which is handy
        # for a quick test but not what the README recommends.
        config.credentials = _credentials_from_parser(parser)

    return config


def _class_list(value: str) -> tuple:
    """Parse `cable_neighbor_classes = network, phone` into a tuple.

    Unrecognised class names are kept rather than rejected — the filter
    simply never matches them — but warned about, because a typo here
    silently narrows what gets cabled.
    """
    from .neighbors import CLASS_AP, CLASS_HOST, CLASS_NETWORK, CLASS_PHONE, CLASS_UNKNOWN

    known = {CLASS_NETWORK, CLASS_PHONE, CLASS_AP, CLASS_HOST, CLASS_UNKNOWN}
    classes = tuple(part.strip() for part in value.split(",") if part.strip())
    for name in classes:
        if name not in known:
            log.warning("cable_neighbor_classes contains %r, which is not one "
                        "of %s — it will never match", name, sorted(known))
    return classes


def load_credentials(path: str) -> list[Credential]:
    """Read an SNMPv3 credentials file, warning if its mode is too open."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"credentials file not found: {path}")
    _warn_if_world_readable(path)

    parser = _parser()
    parser.read(path)
    credentials = _credentials_from_parser(parser)
    if not credentials:
        raise ValueError(
            f"{path} defines no credential sets — each one needs its own "
            f"[{CREDENTIAL_SECTION_PREFIX}:<name>] section"
        )
    return credentials


def _credentials_from_parser(parser: configparser.ConfigParser) -> list[Credential]:
    """Build credentials from every [credential:<name>] section, in file order."""
    credentials = []
    for name in parser.sections():
        if not name.lower().startswith(CREDENTIAL_SECTION_PREFIX):
            continue
        section = parser[name]
        label = name.split(":", 1)[1].strip() if ":" in name else name
        security_name = section.get("security_name", "") or section.get("username", "")
        if not security_name:
            log.warning("credential set %r has no security_name — skipped", label)
            continue
        credentials.append(Credential(
            name=label,
            security_name=security_name,
            auth_protocol=section.get("auth_protocol", "SHA"),
            auth_passphrase=section.get("auth_passphrase", ""),
            priv_protocol=section.get("priv_protocol", "AES"),
            priv_passphrase=section.get("priv_passphrase", ""),
            security_level=section.get("security_level", ""),
            context=section.get("context", ""),
        ))
    return credentials


def _warn_if_world_readable(path: str) -> None:
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        log.warning(
            "%s is readable beyond its owner (mode %s) — it holds SNMPv3 "
            "passphrases; chmod 600 it",
            path, oct(stat.S_IMODE(mode)),
        )
