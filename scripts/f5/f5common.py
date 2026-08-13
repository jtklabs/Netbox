#!/usr/bin/env python3
"""Shared plumbing for the F5 BIG-IP tools in this directory.

Every tool here works the same way: credentials come from a `.env` file (never
from the command line, never from the device list), and the unit is driven over
token-authenticated iControl REST on its management interface. New tools should
import from this module rather than re-implementing any of it.
"""

import csv
import ipaddress
import os
import stat
import sys
import threading
import time
from dataclasses import dataclass, field

try:
    import requests
except ImportError:
    sys.exit("These tools need the 'requests' package: pip install -r requirements.txt")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV_FILE = os.path.join(HERE, ".env")

_print_lock = threading.Lock()


def log(device_name, message):
    with _print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] [{device_name}] {message}", flush=True)


# --------------------------------------------------------------------------- #
# .env loading
# --------------------------------------------------------------------------- #

def load_env_file(path):
    """Read KEY=value pairs from a .env file into a dict.

    Deliberately narrow so there is no dependency to install: comments, blank
    lines, an optional `export ` prefix, and optional surrounding quotes. An
    unquoted value is taken literally to the end of the line — no inline-comment
    stripping — so a password may contain '#' without being quoted.
    """
    if not os.path.isfile(path):
        template = os.path.join(os.path.dirname(path) or ".", ".env.example")
        sys.exit(f"env file not found: {path}\n"
                 f"  copy the template and fill in F5_USERNAME / F5_PASSWORD:\n"
                 f"  cp {template} {path} && chmod 600 {path}")
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(f"warning: {path} holds credentials and is readable by other users "
              f"— chmod 600 {path}", file=sys.stderr)
    values = {}
    with open(path) as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, sep, value = line.partition("=")
            if not sep:
                sys.exit(f"{path}:{lineno}: expected KEY=value, got: {line}")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return values


@dataclass
class Settings:
    username: str
    password: str
    login_provider: str
    port: int
    verify_ssl: bool
    timeout: int
    workers: int


def load_settings(env_file=None):
    """Build Settings from a .env file, with the real environment taking
    precedence — a CI job or vault wrapper can export F5_PASSWORD instead of
    writing the secret to disk."""
    path = env_file or DEFAULT_ENV_FILE
    values = load_env_file(path)

    def get(key, default=""):
        return os.environ.get(key) or values.get(key) or default

    def get_int(key, default):
        raw = get(key, str(default))
        try:
            return int(raw)
        except ValueError:
            sys.exit(f"{key} must be a whole number, got: {raw!r}")

    missing = [key for key in ("F5_USERNAME", "F5_PASSWORD") if not get(key)]
    if missing:
        sys.exit(f"{path} is missing {' and '.join(missing)} "
                 f"(see .env.example)")
    return Settings(
        username=get("F5_USERNAME"),
        password=get("F5_PASSWORD"),
        login_provider=get("F5_LOGIN_PROVIDER", "tmos"),
        port=get_int("F5_PORT", 443),
        verify_ssl=get("F5_VERIFY_SSL", "false").lower() in ("1", "true", "yes", "on"),
        timeout=get_int("F5_TIMEOUT", 60),
        workers=get_int("F5_WORKERS", 5),
    )


# --------------------------------------------------------------------------- #
# Standards file (platform-neutral, shared by every platform's tooling)
# --------------------------------------------------------------------------- #

DEFAULT_STANDARDS_FILE = os.path.normpath(os.path.join(HERE, os.pardir, "standards.yaml"))


@dataclass
class Destination:
    """One syslog collector: an address (or hostname) and a port."""

    host: str
    port: int = 514

    @property
    def label(self):
        return f"{self.host}:{self.port}"

    @property
    def key(self):
        """What makes two destinations the same collector. The name a device
        happens to file it under is cosmetic and deliberately not part of this."""
        try:
            host = str(ipaddress.ip_address(self.host))
        except ValueError:
            host = self.host.strip().lower()
        return host, self.port


@dataclass
class Standards:
    """The parsed standards file. One field per standard."""

    path: str
    snmp_allow: list = field(default_factory=list)      # [(text, ip_network)]
    syslog: list = field(default_factory=list)          # [Destination]


def load_standards(path=None):
    """Read and validate the platform-neutral standards file.

    It lives here because the F5 tools are its only reader today; when a second
    platform's tooling arrives, this belongs in a module both can import.
    """
    try:
        import yaml
    except ImportError:
        sys.exit("Reading the standards file needs the 'PyYAML' package: "
                 "pip install -r requirements.txt")
    path = path or DEFAULT_STANDARDS_FILE
    if not os.path.isfile(path):
        sys.exit(f"standards file not found: {path}")
    with open(path) as fh:
        try:
            doc = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            sys.exit(f"{path} is not valid YAML: {exc}")
    if not isinstance(doc, dict):
        sys.exit(f"{path} must be a mapping of standard name to settings")

    # A typo'd key would otherwise mean "this standard is not defined", which
    # reads as compliant and silently enforces nothing.
    known = {"snmp": {"allow"}, "syslog": {"destinations"}}
    for key, section in doc.items():
        if key not in known:
            print(f"warning: {path}: ignoring unknown standard {key!r} "
                  f"(known: {', '.join(sorted(known))})", file=sys.stderr)
            continue
        if section is None:
            continue
        if not isinstance(section, dict):
            sys.exit(f"{path}: {key} must be a mapping, e.g. "
                     f"{key}: {{{sorted(known[key])[0]}: [...]}}")
        for sub in section:
            if sub not in known[key]:
                print(f"warning: {path}: ignoring unknown key '{key}.{sub}' "
                      f"(known: {', '.join(f'{key}.{k}' for k in sorted(known[key]))})",
                      file=sys.stderr)

    standards = Standards(path=path)
    for text in _as_list(doc.get("snmp") or {}, "allow", path, "snmp"):
        try:
            standards.snmp_allow.append(normalize_network(str(text)))
        except ValueError as exc:
            sys.exit(f"{path}: snmp.allow: {exc}")
    for item in _as_list(doc.get("syslog") or {}, "destinations", path, "syslog"):
        standards.syslog.append(_parse_destination(item, path))
    return standards


def _as_list(section, key, path, label):
    value = section.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        sys.exit(f"{path}: {label}.{key} must be a list")
    return value


def _parse_destination(item, path):
    """Accept either the shorthand `- 10.1.1.50` or `- {host: ..., port: ...}`."""
    where = f"{path}: syslog.destinations"
    if isinstance(item, dict):
        unknown = set(item) - {"host", "port"}
        if unknown:
            sys.exit(f"{where}: unknown key(s) {', '.join(sorted(unknown))} "
                     f"— only host and port are supported")
        host, port = item.get("host"), item.get("port", 514)
    else:
        host, port = item, 514
    if not host or not str(host).strip():
        sys.exit(f"{where}: every destination needs a host")
    host = str(host).strip()
    try:
        port = int(port)
    except (TypeError, ValueError):
        sys.exit(f"{where}: port for {host} must be a whole number, got {port!r}")
    if not 1 <= port <= 65535:
        sys.exit(f"{where}: port for {host} must be between 1 and 65535, got {port}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # A hostname is legal — the device resolves it — but a mistyped address
        # would otherwise sail through as one, so say what was assumed.
        if any(ch.isdigit() for ch in host.split(".")[-1]):
            sys.exit(f"{where}: {host!r} is neither a valid IP address nor a hostname")
    return Destination(host=host, port=port)


# --------------------------------------------------------------------------- #
# Device inventory
# --------------------------------------------------------------------------- #

@dataclass
class Device:
    host: str
    name: str


def load_devices(csv_path):
    """Inventory only (host + optional display name). Credentials live in .env,
    so no secrets ever end up in a device list."""
    if not os.path.isfile(csv_path):
        sys.exit(f"device CSV not found: {csv_path} (copy devices.csv.example and fill it in)")
    devices = []
    with open(csv_path, newline="") as fh:
        rows = (row for row in fh if row.strip() and not row.lstrip().startswith("#"))
        for row in csv.DictReader(rows):
            host = (row.get("host") or "").strip()
            if not host:
                continue
            devices.append(Device(host=host, name=(row.get("name") or "").strip() or host))
    if not devices:
        sys.exit(f"no devices found in {csv_path} (needs a 'host' column)")
    return devices


# --------------------------------------------------------------------------- #
# iControl REST
# --------------------------------------------------------------------------- #

def raise_for_status(resp):
    """F5 puts the useful part of a failure in the response body ("...must be
    a valid IP address"), which requests' own error string drops."""
    if resp.ok:
        return
    detail = ""
    try:
        detail = (resp.json() or {}).get("message", "")
    except ValueError:
        detail = resp.text[:200]
    where = f"{resp.request.method} {resp.request.path_url}"
    raise RuntimeError(f"HTTP {resp.status_code} on {where}" + (f": {detail}" if detail else ""))


def _root_cause(exc):
    """The innermost exception requests/urllib3 wrapped."""
    while True:
        nested = getattr(exc, "reason", None)
        if not isinstance(nested, BaseException):
            nested = next((a for a in getattr(exc, "args", ()) if isinstance(a, BaseException)), None)
        if nested is None:
            return exc
        exc = nested


def error_text(exc, base=""):
    """A one-liner an operator can act on.

    requests nests urllib3's own repr — "('Connection aborted.',
    ConnectionResetError(104, 'Connection reset by peer'))" — which reads as
    Python source rather than as a fault. Anything already carrying a good
    message (RuntimeError from raise_for_status) is passed through.
    """
    where = f" {base}" if base else ""
    if isinstance(exc, requests.exceptions.SSLError):
        return (f"TLS verification failed for{where} — install the management CA "
                f"or set F5_VERIFY_SSL=false in .env")
    if isinstance(exc, requests.exceptions.Timeout):
        return f"timed out talking to{where} — raise F5_TIMEOUT if the unit is just slow"
    if isinstance(exc, requests.exceptions.ConnectionError):
        detail = str(_root_cause(exc))
        _, sep, tail = detail.partition("Failed to establish a new connection: ")
        return f"cannot reach{where} — {tail if sep else detail}"
    return str(exc)


class F5Client:
    """Minimal token-authenticated iControl REST client for one unit."""

    def __init__(self, device, settings):
        self.device = device
        self.settings = settings
        self.timeout = settings.timeout
        self.base = f"https://{device.host}:{settings.port}"
        self.session = requests.Session()
        # Passed per-request rather than via session.verify: a REQUESTS_CA_BUNDLE
        # env var would silently override the session-level setting.
        self.verify = settings.verify_ssl
        self.token = None

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc_info):
        self.logout()
        return False

    def login(self):
        resp = self.session.post(
            f"{self.base}/mgmt/shared/authn/login",
            json={
                "username": self.settings.username,
                "password": self.settings.password,
                "loginProviderName": self.settings.login_provider,
            },
            verify=self.verify,
            timeout=self.timeout,
        )
        if resp.status_code in (401, 403):
            raise RuntimeError("authentication failed — check F5_USERNAME / F5_PASSWORD "
                               "(and F5_LOGIN_PROVIDER for remote auth)")
        raise_for_status(resp)
        self.token = resp.json()["token"]["token"]
        self.session.headers["X-F5-Auth-Token"] = self.token

    def logout(self):
        if not self.token:
            return
        try:
            self.session.delete(
                f"{self.base}/mgmt/shared/authz/tokens/{self.token}",
                verify=self.verify, timeout=self.timeout,
            )
        except requests.RequestException:
            pass
        self.token = None

    def get_json(self, path):
        resp = self.session.get(f"{self.base}{path}", verify=self.verify, timeout=self.timeout)
        raise_for_status(resp)
        return resp.json()

    def patch_json(self, path, payload, timeout=None):
        resp = self.session.patch(f"{self.base}{path}", json=payload, verify=self.verify,
                                  timeout=timeout or self.timeout)
        raise_for_status(resp)
        return resp.json()

    def post_json(self, path, payload, timeout=None):
        resp = self.session.post(f"{self.base}{path}", json=payload, verify=self.verify,
                                 timeout=timeout or self.timeout)
        raise_for_status(resp)
        return resp.json()

    def save_config(self):
        """Persist the running config to disk — the tmsh `save sys config`.

        REST writes land in the running config only; without this the change is
        lost at the next reboot or config reload.
        """
        self.post_json("/mgmt/tm/sys/config", {"command": "save"},
                       timeout=max(self.timeout, 120))


# --------------------------------------------------------------------------- #
# Address specs (allow lists: sys snmp allowed-addresses, sys sshd allow, ...)
# --------------------------------------------------------------------------- #

def parse_address_spec(text):
    """Return an ip_network for a BIG-IP allow-list entry, or None if it isn't
    one.

    BIG-IP accepts a bare address (10.1.1.5), CIDR (10.1.1.0/24), an address
    with netmask (10.1.1.0/255.255.255.0) and hostnames. Hostnames — and
    anything else unparseable — come back None so callers fall back to
    comparing the literal string.
    """
    try:
        return ipaddress.ip_network(text.strip(), strict=False)
    except ValueError:
        return None


def normalize_network(text):
    """Validate one user-supplied network and return (bigip_string, network).

    A network with host bits set (10.1.1.5/24) is rejected rather than silently
    widened: on an allow list, guessing whether the subnet or the single host
    was meant is not ours to do.
    """
    text = text.strip()
    try:
        net = ipaddress.ip_network(text, strict=True)
    except ValueError as exc:
        loose = parse_address_spec(text)
        if loose is not None:
            host = ipaddress.ip_network(text.split("/")[0].strip(), strict=False)
            raise ValueError(
                f"{text} has host bits set — use {loose.with_prefixlen} for the whole "
                f"subnet, or {host.network_address} for just that host"
            ) from exc
        raise ValueError(f"{text} is not a valid IP address or network ({exc})") from exc
    return bigip_form(net), net


def bigip_form(net):
    """How BIG-IP itself writes a network in an allow list: single addresses
    bare, everything else in CIDR."""
    if net.prefixlen == net.max_prefixlen:
        return str(net.network_address)
    return str(net)


def covers(entry, wanted):
    """True if allow-list entry `entry` (an ip_network) already permits
    `wanted`."""
    if entry.version != wanted.version:
        return False
    return wanted.subnet_of(entry)
