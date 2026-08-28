"""NetBox as the inventory, and per-device values that come from it.

Two things arrive together here, because the second is the reason for the first.

**Devices.** Active devices with a primary IP become hosts. The platform comes
from NetBox's platform slug, and site, role, tags and device custom fields land
in host data, so `--filter site=atl` works exactly as it does with a CSV.

**Interface custom fields.** A source interface is a property of the device, not
of the fleet: one switch sources syslog from Loopback0, another from Vlan10,
and a third from nothing at all. That is recorded in NetBox as a boolean custom
field on the *interface* -- `ntp_source_interface` set true on the one interface
that is the source.

The rule that follows from a boolean:

* no interface marked -> the device uses no source interface;
* exactly one -> that interface;
* two or more -> the device is in an ambiguous state nobody meant, so that
  device fails with a message naming the interfaces. Picking one would be
  guessing, and picking the wrong source is the kind of thing that quietly
  breaks return traffic.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from nornir.core.inventory import (
    ConnectionOptions,
    Defaults,
    Groups,
    Host,
    Hosts,
    Inventory,
)
from nornir.core.plugins.inventory import InventoryPluginRegister

from .core import canonical_platform

#: NetBox platform slug -> the netmiko device type to connect with, which is
#: also the `templates/<platform>/` directory.
#:
#: The names on the left are what `scripts/snmp-inventory` writes: it sets a
#: NetBox Platform per OS family ("Cisco IOS", "Cisco IOS-XE", "Cisco NX-OS",
#: "Arista EOS", "Junos", ...) and NetBox slugifies the name. Left to the
#: generic hyphen-to-underscore rule, "Cisco NX-OS" would become `cisco_nx_os`
#: -- not a netmiko driver -- and the device would fail on connect with
#: something unhelpful.
#:
#: Platforms this tool has no templates for are mapped anyway. A device then
#: fails with "platform 'cisco_nxos' has no 'ntp' support", which is accurate
#: and costs no connection, rather than being dialled and misunderstood.
NETBOX_PLATFORMS = {
    # netmiko's cisco_ios driver speaks to both, and one template covers both.
    "cisco-ios": "cisco_ios",
    "cisco-ios-xe": "cisco_ios",
    "cisco-nx-os": "cisco_nxos",
    "cisco-asa": "cisco_asa",
    "cisco-ios-xr": "cisco_xr",
    "arista-eos": "arista_eos",
    "junos": "juniper_junos",
    "pan-os": "paloalto_panos",
    "fortios": "fortinet",
    "f5-tmos": "f5_tmsh",
    "check-point-gaia": "checkpoint_gaia",
    "arubaos": "aruba_os",
    "arubaos-cx": "aruba_aoscx",
    "opengear": "opengear_linux",
    # No netmiko driver exists for these. Naming them is still better than
    # leaving them blank: blank means "autodetect", which spends a login
    # finding out what we already know.
    "aruba-clearpass": "aruba_clearpass",
    "infoblox-nios": "infoblox_nios",
    "sgos": "bluecoat_sgos",
}

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """NetBox's own rule: lowercase, non-alphanumerics collapsed to hyphens.

    Applied to whatever NetBox gave us, so a display name ("Cisco IOS-XE")
    resolves the same as the slug it would have been given.
    """
    return _SLUG_STRIP.sub("-", str(value).strip().lower()).strip("-")

#: Interface custom fields consulted by default. The part before
#: `_source_interface` is the feature the answer belongs to.
DEFAULT_SOURCE_FIELDS = ("ntp_source_interface", "syslog_source_interface")

_SOURCE_SUFFIX = "_source_interface"

#: NetBox pages at 50 by default, which is a lot of round trips for a fleet.
PAGE_SIZE = 250


class NetBoxError(Exception):
    """NetBox could not be reached, or answered with something unusable."""


class AmbiguousSource(Exception):
    """More than one interface claims to be the source for one standard."""


def feature_of(field: str) -> str:
    """`ntp_source_interface` -> `ntp`."""
    return field[: -len(_SOURCE_SUFFIX)] if field.endswith(_SOURCE_SUFFIX) else field


class Client:
    """The handful of NetBox reads this needs."""

    def __init__(self, url: str, token: str, verify_tls: bool = True, timeout: float = 30.0):
        if not url:
            raise NetBoxError("no NetBox URL: set $NETBOX_URL or --netbox-url")
        if not token:
            raise NetBoxError("no NetBox token: set $NETBOX_TOKEN or --netbox-secret")
        self.url = url.rstrip("/")
        self.token = token
        self.verify_tls = verify_tls
        self.timeout = timeout
        self._session = None

    def session(self):
        if self._session is None:
            try:
                import requests
            except ImportError as exc:  # pragma: no cover - depends on extras
                raise NetBoxError(
                    "NetBox support needs the 'requests' package "
                    "(pip install -r requirements.txt)"
                ) from exc
            self._session = requests.Session()
            self._session.headers.update(
                {"Authorization": f"Token {self.token}", "Accept": "application/json"}
            )
        return self._session

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        """Every page of an endpoint, followed to the end."""
        query = dict(params or {})
        query.setdefault("limit", PAGE_SIZE)
        url = f"{self.url}/api/{path.lstrip('/')}"
        results: List[Dict[str, Any]] = []

        while url:
            response = self.session().get(
                url, params=query, timeout=self.timeout, verify=self.verify_tls
            )
            if response.status_code >= 400:
                raise NetBoxError(
                    f"GET {path} failed ({response.status_code}): "
                    f"{' '.join(response.text.split())[:200]}"
                )
            try:
                document = response.json()
            except ValueError as exc:
                raise NetBoxError(f"GET {path}: response was not JSON") from exc
            results.extend(document.get("results", []))
            url = document.get("next")
            query = {}  # the `next` URL already carries the query
        return results


# --------------------------------------------------------------------------- #
# mapping NetBox to hosts
# --------------------------------------------------------------------------- #


def _address(device: Mapping[str, Any]) -> Optional[str]:
    """The primary IP, without its mask. v4 first, then v6."""
    for key in ("primary_ip4", "primary_ip6", "primary_ip"):
        record = device.get(key)
        if isinstance(record, Mapping) and record.get("address"):
            return str(record["address"]).split("/")[0]
    return None


def _slug(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        return value.get("slug") or value.get("value") or value.get("name")
    return value if isinstance(value, str) else None


def platform_of(device: Mapping[str, Any]) -> Optional[str]:
    """The netmiko device type for a NetBox platform.

    The explicit table first, because an OS family's NetBox name rarely
    resembles its netmiko driver; then the generic hyphen-to-underscore rule
    for anything the table has not met yet.
    """
    value = _slug(device.get("platform"))
    if not value:
        return None
    known = NETBOX_PLATFORMS.get(slugify(value))
    if known:
        return known
    return canonical_platform(str(value).replace("-", "_")) or None


def device_data(device: Mapping[str, Any]) -> Dict[str, Any]:
    """What `--filter` can select on."""
    role = device.get("role") or device.get("device_role")  # renamed in NetBox 3.6
    data: Dict[str, Any] = {
        "netbox_id": device.get("id"),
        "site": _slug(device.get("site")),
        "role": _slug(role),
        "status": _slug(device.get("status")),
        "tenant": _slug(device.get("tenant")),
        "manufacturer": _slug((device.get("device_type") or {}).get("manufacturer")),
        "model": _slug(device.get("device_type")),
        "tags": ",".join(sorted(filter(None, (_slug(t) for t in device.get("tags") or [])))),
    }
    for key, value in (device.get("custom_fields") or {}).items():
        data.setdefault(key, _slug(value) if isinstance(value, Mapping) else value)
    return {key: value for key, value in data.items() if value is not None}


def resolve_sources(
    interfaces: Iterable[Mapping[str, Any]], field: str
) -> Tuple[Dict[int, str], Dict[int, List[str]]]:
    """Group interfaces claiming `field` by device.

    Returns the single source per device, and separately the devices where more
    than one interface claims it -- which is not something to resolve by
    picking one.
    """
    claimed: Dict[int, List[str]] = {}
    for interface in interfaces:
        device = interface.get("device") or {}
        device_id = device.get("id")
        name = interface.get("name")
        if device_id is None or not name:
            continue
        claimed.setdefault(int(device_id), []).append(str(name))

    single = {
        device_id: names[0] for device_id, names in claimed.items() if len(names) == 1
    }
    ambiguous = {
        device_id: sorted(names) for device_id, names in claimed.items() if len(names) > 1
    }
    return single, ambiguous


def source_interfaces(
    client: Client, fields: Sequence[str], filters: Mapping[str, Any]
) -> Dict[int, Dict[str, Any]]:
    """One query per custom field, for the whole fleet at once.

    Asking per device would be one round trip per device per standard; asking
    NetBox for every interface with the field set is a single query that the
    server is built to answer.
    """
    per_device: Dict[int, Dict[str, Any]] = {}
    for field in fields:
        query = {f"cf_{field}": "true", **filters}
        interfaces = client.get("dcim/interfaces/", query)
        single, ambiguous = resolve_sources(interfaces, field)
        feature = feature_of(field)
        for device_id, name in single.items():
            per_device.setdefault(device_id, {}).setdefault("source_interface", {})[
                feature
            ] = name
        for device_id, names in ambiguous.items():
            per_device.setdefault(device_id, {}).setdefault("source_interface_error", {})[
                feature
            ] = (
                f"{len(names)} interfaces are marked {field} in NetBox "
                f"({', '.join(names)}); exactly one may be"
            )
    return per_device


class NetBoxInventory:
    """Nornir inventory plugin backed by NetBox."""

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        filters: Optional[Mapping[str, Any]] = None,
        source_fields: Sequence[str] = DEFAULT_SOURCE_FIELDS,
        verify_tls: bool = True,
        username: Optional[str] = None,
        password: Optional[str] = None,
        secret: Optional[str] = None,
        key_file: Optional[str] = None,
        conn_timeout: Optional[float] = None,
        port: int = 22,
        client: Optional[Client] = None,
    ) -> None:
        self.client = client or Client(
            url or os.environ.get("NETBOX_URL", ""),
            token or os.environ.get("NETBOX_TOKEN", ""),
            verify_tls,
        )
        self.filters = dict(filters or {})
        self.source_fields = tuple(source_fields)
        self.username = username
        self.password = password
        self.secret = secret
        self.key_file = key_file
        self.conn_timeout = conn_timeout
        self.port = port

    def load(self) -> Inventory:
        query = {"status": "active", "has_primary_ip": "true", **self.filters}
        devices = self.client.get("dcim/devices/", query)
        if not devices:
            raise NetBoxError(
                f"NetBox returned no devices for {query} -- check the filters, and "
                f"that the devices are active and have a primary IP"
            )

        extras: Dict[str, Any] = {}
        if self.secret:
            extras["secret"] = self.secret
        if self.key_file:
            extras["use_keys"] = True
            extras["key_file"] = self.key_file
        if self.conn_timeout:
            extras["conn_timeout"] = self.conn_timeout
        defaults = Defaults(
            username=self.username,
            password=self.password,
            port=self.port,
            connection_options={"netmiko": ConnectionOptions(extras=dict(extras))},
        )

        sources = (
            source_interfaces(self.client, self.source_fields, self.filters)
            if self.source_fields
            else {}
        )

        hosts = Hosts()
        skipped: List[str] = []
        for device in devices:
            name = device.get("name") or f"device-{device.get('id')}"
            address = _address(device)
            if not address:
                skipped.append(str(name))
                continue
            data = device_data(device)
            # An empty mapping still means "NetBox was asked", which is what
            # tells a feature that the answer here is authoritative.
            data["source_interface"] = {}
            data["source_interface_error"] = {}
            data.update(sources.get(device.get("id"), {}))
            hosts[str(name)] = Host(
                name=str(name),
                hostname=address,
                platform=platform_of(device),
                data=data,
                defaults=defaults,
            )

        if not hosts:
            raise NetBoxError("no NetBox device had a primary IP to connect to")
        return Inventory(hosts=hosts, groups=Groups(), defaults=defaults)


InventoryPluginRegister.register("netbox", NetBoxInventory)


def parse_filters(pairs: Sequence[str]) -> Dict[str, Any]:
    """`--netbox-filter site=atl` into API query parameters.

    A repeated key becomes a list, because NetBox reads repeated parameters as
    "any of these" -- `site=atl --netbox-filter site=rdu` means both sites.
    """
    filters: Dict[str, Any] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise NetBoxError(f"--netbox-filter needs KEY=VALUE, got {pair!r}")
        key, value = key.strip(), value.strip()
        if key in filters:
            existing = filters[key]
            filters[key] = existing + [value] if isinstance(existing, list) else [existing, value]
        else:
            filters[key] = value
    return filters


def source_for(host, feature: str) -> Tuple[Optional[str], bool]:
    """This device's source interface for one standard.

    Returns (interface, authoritative). `authoritative` is False when the
    inventory never had an opinion -- a CSV, say -- so the caller falls back to
    the fleet-wide value from the standards file. When it is True, "no
    interface" is a real answer and means no source interface.
    """
    data = getattr(host, "data", {}) or {}
    if "source_interface" not in data:
        return None, False
    problem = (data.get("source_interface_error") or {}).get(feature)
    if problem:
        raise AmbiguousSource(problem)
    return (data.get("source_interface") or {}).get(feature), True


def settings_from(standards, args) -> Dict[str, Any]:
    """Where NetBox is and what to ask it, from the standards file and flags.

    The token is never taken from the standards file -- that file is meant to
    be committed.
    """
    section = standards.section("netbox") if standards is not None else {}
    fields = getattr(args, "netbox_source_field", None) or section.get("source_fields")
    return {
        "url": getattr(args, "netbox_url", None) or section.get("url") or os.environ.get("NETBOX_URL"),
        "token": os.environ.get("NETBOX_TOKEN"),
        "filters": parse_filters(getattr(args, "netbox_filter", None) or []),
        "source_fields": tuple(fields) if fields else DEFAULT_SOURCE_FIELDS,
        "verify_tls": str(section.get("verify_tls", "true")).lower() != "false",
    }


def init_nornir(args, credentials, standards, workers: int):
    """A Nornir instance whose inventory is NetBox."""
    from nornir import InitNornir

    settings = settings_from(standards, args)
    if getattr(args, "netbox_secret", None):
        from .credentials import fetch_json_secret

        document = fetch_json_secret(args.netbox_secret, getattr(args, "aws_region", None))
        settings["token"] = settings["token"] or document.get("token")
        settings["url"] = settings["url"] or document.get("url")

    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": workers}},
        inventory={
            "plugin": "netbox",
            "options": {
                **settings,
                "username": credentials.username,
                "password": credentials.password,
                "secret": credentials.secret,
                "key_file": args.key_file,
                "conn_timeout": args.conn_timeout,
                "port": args.port,
            },
        },
        logging={"enabled": False},
    )
