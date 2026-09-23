"""NetBox as the inventory, and per-device values that come from it.

Two things arrive together here, because the second is the reason for the first.

**Devices.** Active devices with a primary IP become hosts. The platform comes
from NetBox's platform slug, and site, role, tags and device custom fields land
in host data, so `--filter site=atl` works exactly as it does with a CSV.

**Interface tags.** A source interface is a property of the device, not of the
fleet: one switch sources syslog from Loopback0, another from Vlan10, and a
third from nothing at all. That is recorded in NetBox as a *tag* on the
interface -- `ntp-source` on the one interface that is the source.

The rule that follows:

* no interface tagged -> the device uses no source interface;
* exactly one -> that interface;
* two or more -> the device is in an ambiguous state nobody meant, so that
  device fails with a message naming the interfaces. Picking one would be
  guessing, and picking the wrong source is the kind of thing that quietly
  breaks return traffic.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from nornir.core.inventory import Inventory

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

#: Interface tags consulted by default, as feature -> tag slug. A tag is a
#: slug in NetBox, so `ntp-source` rather than `ntp_source_interface`.
DEFAULT_SOURCE_TAGS = {"ntp": "ntp-source", "syslog": "syslog-source"}

_SOURCE_SUFFIX = "-source"

#: NetBox pages at 50 by default, which is a lot of round trips for a fleet.
PAGE_SIZE = 250


class NetBoxError(Exception):
    """NetBox could not be reached, or answered with something unusable."""


class AmbiguousSource(Exception):
    """More than one interface claims to be the source for one standard."""


def feature_of(tag: str) -> str:
    """`ntp-source` -> `ntp`."""
    return tag[: -len(_SOURCE_SUFFIX)] if tag.endswith(_SOURCE_SUFFIX) else tag


def source_tags(configured: Any) -> Dict[str, str]:
    """Normalize what the standards file or the flags asked for.

    Accepts a mapping of feature to tag, or a bare list of tags whose feature
    is the part before `-source`.
    """
    if not configured:
        return dict(DEFAULT_SOURCE_TAGS)
    if isinstance(configured, Mapping):
        return {str(feature): str(tag) for feature, tag in configured.items()}
    if isinstance(configured, str) or not isinstance(configured, Sequence):
        raise NetBoxError("netbox.source_tags must be a mapping or a list of tag slugs")
    return {feature_of(str(tag)): str(tag) for tag in configured}


class Client:
    """Inventory reads and narrowly scoped audit timestamp writes."""

    def __init__(self, url: str, token: str, verify_tls: bool = False, timeout: float = 30.0):
        if not url:
            raise NetBoxError("no NetBox URL: set $NETBOX_URL or --netbox-url")
        if not token:
            raise NetBoxError("no NetBox token: set $NETBOX_TOKEN or --netbox-secret")
        self.url = url.rstrip("/")
        self.token = token
        if not verify_tls:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.verify_tls = verify_tls
        self.timeout = timeout
        self._session = None
        from .debuglog import protect
        protect([token])

    def session(self):
        if self._session is None:
            try:
                import requests
            except ImportError as exc:  # pragma: no cover - depends on extras
                raise NetBoxError(
                    "NetBox support needs the 'requests' package "
                    "(pip install 'requests>=2.31')"
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
        session = self.session()
        from requests import RequestException

        while url:
            try:
                response = session.get(
                    url, params=query, timeout=self.timeout, verify=self.verify_tls
                )
            except RequestException as exc:
                raise NetBoxError(f"GET {path}: could not reach NetBox: {exc}") from exc
            if response.status_code >= 400:
                raise NetBoxError(
                    f"GET {path} failed ({response.status_code}): "
                    f"{' '.join(response.text.split())[:200]}"
                )
            try:
                document = response.json()
            except ValueError as exc:
                raise NetBoxError(f"GET {path}: response was not JSON") from exc
            if (
                not isinstance(document, dict)
                or not isinstance(document.get("results"), list)
                or any(not isinstance(item, dict) for item in document["results"])
                or (document.get("next") is not None and not isinstance(document["next"], str))
            ):
                raise NetBoxError(f"GET {path}: expected a paginated NetBox response")
            results.extend(document["results"])
            url = document.get("next")
            query = {}  # the `next` URL already carries the query
        return results

    def request_object(self, method: str, path: str, payload=None) -> Dict[str, Any]:
        from requests import RequestException

        try:
            response = self.session().request(
                method, f"{self.url}/api/{path.lstrip('/')}", json=payload,
                timeout=self.timeout, verify=self.verify_tls,
            )
        except RequestException as exc:
            raise NetBoxError(f"{method} {path}: could not reach NetBox: {exc}") from exc
        if response.status_code >= 400:
            raise NetBoxError(f"{method} {path} failed ({response.status_code}): "
                              f"{' '.join(response.text.split())[:200]}")
        try:
            document = response.json()
        except ValueError as exc:
            raise NetBoxError(f"{method} {path}: response was not JSON") from exc
        if not isinstance(document, dict):
            raise NetBoxError(f"{method} {path}: expected a NetBox object")
        return document

    def _has_device_field(self, name: str, expected_type: str) -> bool:
        """Validate an existing device field's type and object assignment."""
        fields = self.get("extras/custom-fields/", {"name": name})
        if fields:
            if len(fields) != 1 or fields[0].get("name") != name:
                raise NetBoxError(f"custom field lookup for {name!r} was ambiguous")
            field = fields[0]
            kind = field.get("type")
            if isinstance(kind, dict):
                kind = kind.get("value")
            if kind != expected_type or "dcim.device" not in field.get("object_types", []):
                raise NetBoxError(f"{name} must be a {expected_type} custom field assigned to dcim.device")
            return True
        return False

    def require_boolean_field(self, name: str) -> None:
        if not self._has_device_field(name, "boolean"):
            raise NetBoxError(f"missing boolean device custom field {name}")

    def ensure_datetime_field(self, name: str, apply: bool = False) -> bool:
        """Validate the device field. Return True when it needs creating."""
        if self._has_device_field(name, "datetime"):
            return False
        if apply:
            self.request_object("POST", "extras/custom-fields/", {
                "name": name, "label": "Syslog last checked", "type": "datetime",
                "object_types": ["dcim.device"], "required": False,
                "description": "Last completed syslog audit or verified configuration check; "
                               "does not imply compliance. Failed checks leave this date unchanged.",
            })
        return True

    def stamp_device(self, device_id: int, field: str, checked_at: str,
                     compliant: Optional[bool] = None) -> None:
        """Write the check date and known exact-match verdict in one PATCH."""
        from datetime import datetime

        path = f"dcim/devices/{int(device_id)}/"
        fields = {field: checked_at}
        if compliant is not None:
            fields["syslog_compliant"] = compliant
        self.request_object("PATCH", path, {"custom_fields": fields})
        device = self.request_object("GET", path)
        observed = (device.get("custom_fields") or {}).get(field)
        try:
            matches = (datetime.fromisoformat(str(observed).replace("Z", "+00:00")) ==
                       datetime.fromisoformat(checked_at.replace("Z", "+00:00")))
        except ValueError:
            matches = False
        if not matches:
            raise NetBoxError(f"device {device_id}: {field} timestamp did not verify after the write")
        if compliant is not None and (device.get("custom_fields") or {}).get("syslog_compliant") is not compliant:
            raise NetBoxError(f"device {device_id}: syslog_compliant did not verify after the write")


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
    interfaces: Iterable[Mapping[str, Any]], tag: str
) -> Tuple[Dict[int, str], Dict[int, List[str]]]:
    """Group interfaces carrying `tag` by device.

    Returns the single source per device, and separately the devices where more
    than one interface carries it -- which is not something to resolve by
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
    client: Client, tags: Mapping[str, str]
) -> Dict[int, Dict[str, Any]]:
    """One query per tag, for the whole fleet at once.

    Asking per device would be one round trip per device per standard; asking
    NetBox for every interface carrying the tag is a single query that the
    server is built to answer.
    """
    per_device: Dict[int, Dict[str, Any]] = {}
    for feature, tag in tags.items():
        # Device filters belong to dcim/devices: name, tag, id and custom
        # fields mean something different on interfaces. Join by device ID
        # below, after the inventory has selected the devices.
        query = {"tag": tag}
        interfaces = client.get("dcim/interfaces/", query)
        single, ambiguous = resolve_sources(interfaces, tag)
        for device_id, name in single.items():
            per_device.setdefault(device_id, {}).setdefault("source_interface", {})[
                feature
            ] = name
        for device_id, names in ambiguous.items():
            per_device.setdefault(device_id, {}).setdefault("source_interface_error", {})[
                feature
            ] = (
                f"{len(names)} interfaces are tagged {tag} in NetBox "
                f"({', '.join(names)}); exactly one may be"
            )
    return per_device


def site_regions(client: Client, devices: Sequence[Mapping[str, Any]]) -> Dict[int, List[str]]:
    """Each site's region slugs, nearest first and up to the root.

    Two queries for the whole run: the sites the devices sit in, and every
    region. The device API names the site but not its region, and a standard
    set for "us" has to reach a site in a region nested under it.
    """
    site_ids = sorted({int(site["id"]) for site in (device.get("site") for device in devices)
                       if isinstance(site, Mapping) and site.get("id") is not None})
    if not site_ids:
        return {}
    parents: Dict[int, Tuple[Optional[str], Optional[int]]] = {}
    for region in client.get("dcim/regions/"):
        parent = region.get("parent") or {}
        parents[int(region["id"])] = (region.get("slug"), parent.get("id"))
    chains: Dict[int, List[str]] = {}
    for site in client.get("dcim/sites/", {"id": site_ids}):
        chain: List[str] = []
        region_id = (site.get("region") or {}).get("id")
        while region_id is not None and region_id in parents and len(chain) < 32:
            slug, region_id = parents[region_id]
            if slug:
                chain.append(slug)
        chains[int(site["id"])] = chain
    return chains


class NetBoxInventory:
    """Nornir inventory plugin backed by NetBox."""

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        filters: Optional[Mapping[str, Any]] = None,
        source_tags: Optional[Mapping[str, str]] = None,
        verify_tls: bool = False,
        username: Optional[str] = None,
        password: Optional[str] = None,
        secret: Optional[str] = None,
        key_file: Optional[str] = None,
        conn_timeout: Optional[float] = None,
        port: int = 22,
        client: Optional[Client] = None,
        autofilter: bool = False,
        poller: Optional[str] = None,
        regions: bool = False,
    ) -> None:
        self.client = client or Client(
            url or os.environ.get("NETBOX_URL", ""),
            token or os.environ.get("NETBOX_TOKEN", ""),
            verify_tls,
        )
        self.autofilter = autofilter
        self.poller = poller
        self.regions = regions
        self.filters = dict(filters or {})
        self.source_tags = dict(
            DEFAULT_SOURCE_TAGS if source_tags is None else source_tags
        )
        self.username = username
        self.password = password
        self.secret = secret
        self.key_file = key_file
        self.conn_timeout = conn_timeout
        self.port = port

    def load(self) -> Inventory:
        from nornir.core.inventory import ConnectionOptions, Defaults, Groups, Host, Hosts, Inventory

        query = {"status": "active", "has_primary_ip": "true", **self.filters}
        devices = self.client.get("dcim/devices/", query)
        if not devices:
            raise NetBoxError(
                f"NetBox returned no devices for {query} -- check the filters, and "
                f"that the devices are active and have a primary IP"
            )

        ownership = {}
        if self.autofilter:
            from .poller import poller_tag, select_devices
            from . import archive
            summary = {"source": "netbox", "autofilter": True, "poller_tag": poller_tag(self.poller),
                       "api_filters": query, "candidates": len(devices), "status": "resolving"}
            run = archive.current()
            if run:
                run.document["inventory_selection"] = summary
                run.write()
            devices, ownership = select_devices(self.client, devices, self.poller)
            summary.update(selected=len(devices), excluded=summary["candidates"] - len(devices), status="resolved")
            if run:
                run.write()
            if not devices:
                raise NetBoxError(f"NetBox autofilter: no devices belong to {summary['poller_tag']} within the requested filters")

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
            source_interfaces(self.client, self.source_tags)
            if self.source_tags and any(platform_of(device) != "f5_tmsh" for device in devices)
            else {}
        )

        regions = site_regions(self.client, devices) if self.regions else {}

        hosts = Hosts()
        skipped: List[str] = []
        for device in devices:
            name = device.get("name") or f"device-{device.get('id')}"
            address = _address(device)
            if not address:
                skipped.append(str(name))
                continue
            if str(name) in hosts:
                raise NetBoxError(
                    f"duplicate NetBox device name {name!r}; narrow --netbox-filter "
                    "so each inventory host has a unique name"
                )
            data = device_data(device)
            if self.autofilter:
                data["poller_selection"] = ownership[device["id"]]
            # An empty mapping still means "NetBox was asked", which is what
            # tells a feature that the answer here is authoritative.
            data["source_interface"] = {}
            data["source_interface_error"] = {}
            data.update(sources.get(device.get("id"), {}))
            if self.regions:
                # Nearest region first; `region` is the one --filter can select on.
                chain = regions.get((device.get("site") or {}).get("id"), [])
                data["regions"] = chain
                if chain:
                    data["region"] = chain[0]
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


def connection_settings(standards, args) -> Dict[str, Any]:
    """Shared NetBox endpoint, token/secret and TLS settings for reads and writes."""
    section = standards.section("netbox") if standards is not None else {}
    tls = str(os.environ.get("NETBOX_VERIFY_TLS", section.get("verify_tls", False))).strip().lower()
    if tls not in ("true", "false"):
        raise NetBoxError("NETBOX_VERIFY_TLS / netbox.verify_tls must be true or false")
    settings = {
        "url": getattr(args, "netbox_url", None) or section.get("url") or os.environ.get("NETBOX_URL"),
        "token": os.environ.get("NETBOX_TOKEN"),
        "verify_tls": tls == "true",
    }
    if getattr(args, "netbox_secret", None):
        from .credentials import fetch_json_secret

        document = fetch_json_secret(args.netbox_secret, getattr(args, "aws_region", None))
        settings["token"] = settings["token"] or document.get("token")
        settings["url"] = settings["url"] or document.get("url")
    return settings


def settings_from(standards, args) -> Dict[str, Any]:
    """Where NetBox is and what to ask it, from the standards file and flags.

    The token is never taken from the standards file -- that file is meant to
    be committed.
    """
    from .poller import settings_from as poller_settings
    selection = poller_settings(args)
    section = standards.section("netbox") if standards is not None else {}
    tags = getattr(args, "netbox_source_tag", None) or section.get("source_tags")
    return {
        **connection_settings(standards, args),
        "filters": parse_filters(getattr(args, "netbox_filter", None) or
                                 shlex.split(os.environ.get("NETBOX_FILTERS", ""))),
        **selection,
        "source_tags": source_tags(tags),
    }


def init_nornir(args, credentials, standards, workers: int):
    """A Nornir instance whose inventory is NetBox."""
    from nornir import InitNornir
    from nornir.core.plugins.inventory import InventoryPluginRegister

    InventoryPluginRegister.register("netbox", NetBoxInventory)

    settings = settings_from(standards, args)
    if getattr(getattr(args, "feature", None), "name", None) in ("waf", "nac"):
        # WAF and NAC do not use NTP/syslog source-interface tags.
        settings["source_tags"] = {}
    elif getattr(getattr(args, "feature", None), "name", None) == "syslog":
        tag = getattr(args, "syslog_source_tag", None) or settings["source_tags"].get("syslog")
        settings["source_tags"] = {"syslog": tag} if tag else {}
    # Region ancestry costs two extra queries, so only a regional NTP standard asks for it.
    settings["regions"] = bool(
        getattr(getattr(args, "feature", None), "name", None) == "ntp" and not getattr(args, "servers", None)
        and standards is not None and standards.defined("ntp.regions"))
    args._netbox_client = Client(settings["url"], settings["token"], settings["verify_tls"])

    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": workers}},
        inventory={
            "plugin": "netbox",
            "options": {
                **settings,
                "client": args._netbox_client,
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
