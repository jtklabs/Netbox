"""BIG-IP REST discovery and reconciliation for existing WAF logging profiles.

The CLI and NetBox policy live in features/waf.py. This module only reads and
plans remote server lists; it does not assume an inventory or execution mode.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

WAF_PROFILES = "/mgmt/tm/security/log/profile"


class Client:
    """One token-authenticated HTTPS session, using inventory credentials."""

    def __init__(self, host, port=443, verify_tls=True, timeout=30, provider="tmos"):
        import requests

        if not verify_tls:
            import urllib3

            # Verification was explicitly disabled. Install a process-wide
            # category filter so concurrent F5 requests and logout stay quiet.
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        address = host.hostname
        if ":" in address and not address.startswith("["):
            address = f"[{address}]"
        self.base = f"https://{address}:{port}"
        self.username = host.username
        self.password = host.password
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.provider = provider
        self.session = requests.Session()
        self.token = None

    def request(self, method, path, payload=None, timeout=None):
        response = self.session.request(
            method, self.base + path, json=payload, verify=self.verify_tls,
            timeout=timeout or self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"F5 {method} {path} failed ({response.status_code}): "
                               f"{' '.join(response.text.split())[:300]}")
        if method == "DELETE" and (response.status_code == 204 or not response.text):
            return {}
        document = response.json()
        if not isinstance(document, dict):
            raise ValueError(f"F5 {method} {path}: expected a JSON object")
        return document

    def __enter__(self):
        try:
            response = self.request("POST", "/mgmt/shared/authn/login", {
                "username": self.username, "password": self.password,
                "loginProviderName": self.provider,
            })
            self.token = response["token"]["token"]
            from .debuglog import protect
            protect([self.token])
            self.session.headers["X-F5-Auth-Token"] = self.token
            return self
        except Exception:
            self.session.close()
            raise

    def __exit__(self, *args):
        import requests

        try:
            if self.token:
                self.session.delete(
                    self.base + "/mgmt/shared/authz/tokens/" + quote(self.token, safe=""),
                    verify=self.verify_tls, timeout=self.timeout,
                )
        except requests.RequestException:
            pass
        finally:
            self.session.close()

    def get_json(self, path):
        return self.request("GET", path)

    def patch_json(self, path, payload):
        return self.request("PATCH", path, payload)

    def save_config(self):
        return self.request("POST", "/mgmt/tm/sys/config", {"command": "save"},
                            timeout=max(self.timeout, 120))


@dataclass
class Plan:
    label: str
    endpoint: str
    payload: dict
    current: list = field(default_factory=list)
    keep: list = field(default_factory=list)
    add: list = field(default_factory=list)
    extra: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    before_servers: list = field(default_factory=list)

    def drift(self, clean):
        return bool(self.add or (clean and self.extra))


def _waf_path(link):
    """Use REST links on this unit, including BIG-IP's https://localhost links."""
    parsed = urlsplit(link)
    if parsed.path != WAF_PROFILES and not parsed.path.startswith(WAF_PROFILES + "/"):
        raise ValueError(f"unexpected WAF REST path: {parsed.path}")
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _waf_resource(parent, item):
    if item.get("selfLink"):
        return _waf_path(item["selfLink"]).split("?", 1)[0]
    full_path = item.get("fullPath")
    if not full_path:
        name = item.get("name")
        if not name:
            raise ValueError(f"WAF resource without a name under {parent}")
        full_path = "/".join(filter(None, [item.get("partition") or
                                          item.get("tmPartition"),
                                          item.get("subPath"), name]))
    resource = "~".join(full_path.strip("/").split("/"))
    if full_path.startswith("/") or "/" in full_path:
        resource = "~" + resource
    return parent + "/" + quote(resource, safe="~")


def _waf_collection(client, path, first_page=None):
    """Read all pages; never mistake a partial or malformed result for no drift."""
    seen = set()
    while path:
        path = _waf_path(path)
        if path in seen:
            raise ValueError(f"repeated WAF collection page: {path}")
        seen.add(path)
        page = first_page if first_page is not None else client.get_json(path)
        first_page = None
        items = page.get("items", [])
        if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
            raise ValueError(f"invalid WAF collection at {path}")
        yield from items
        path = page.get("nextLink")


def _waf_server_key(server):
    # tmsh uses IPv4:port and IPv6.port, including optional route domains.
    name = str(server.get("name") or "")
    separator = "." if name.count(":") > 1 else ":"
    host, sep, port = name.rpartition(separator)
    if not sep:
        raise ValueError(f"invalid WAF server destination: {name!r}")
    address, _, route_domain = host.partition("%")
    normalized = str(ipaddress.ip_address(address))
    if route_domain and route_domain != "0":
        normalized += "%" + route_domain
    number = int(port)
    if not 1 <= number <= 65535:
        raise ValueError(f"invalid WAF server port: {name!r}")
    return normalized, number


def plan_waf_application(application, wanted, clean, label, endpoint):
    servers = application.get("servers") or []
    if not isinstance(servers, list) or any(not isinstance(s, dict) for s in servers):
        raise ValueError(f"{label}: invalid WAF servers list")
    plan = Plan(label=label, endpoint=endpoint, payload={},
                current=[str(s.get("name") or "?") for s in servers],
                before_servers=list(servers))
    matched, kept = set(), []
    for server in servers:
        key = _waf_server_key(server)
        if key in wanted and key not in matched:
            matched.add(key)
            kept.append(server)
            plan.keep.append(server["name"])
        else:
            plan.extra.append(server["name"])
    added = []
    for host, port in wanted:
        if (host, port) in matched:
            continue
        name = f"{host}{'.' if ':' in host else ':'}{port}"
        plan.add.append(name)
        added.append({"name": name})
    # Patch the application subresource only, preserving protocol, storage
    # format, filters, local logging and all other security logging categories.
    plan.payload = {"servers": (kept if clean else servers) + added}
    return plan


def plan_waf(client, wanted, clean, skipped_profiles=None):
    """Discover user-defined application profiles already logging remotely."""
    plans = []
    for profile in _waf_collection(client, WAF_PROFILES):
        endpoint = _waf_resource(WAF_PROFILES, profile)
        label = profile.get("fullPath") or profile.get("name") or endpoint
        # F5's builtIn flag identifies predefined profiles independently of
        # names, partitions and release-specific additions. Never infer that a
        # profile belongs to the operator merely because its name is unfamiliar.
        built_in = str(profile.get("builtIn", "")).strip().lower()
        if built_in not in ("disabled", "false"):
            known_builtin = built_in in ("enabled", "true")
            if skipped_profiles is not None:
                skipped_profiles.append({
                    "profile": label,
                    "reason": "F5 built-in profile" if known_builtin else
                              "cannot confirm user-defined profile: builtIn missing or unrecognized",
                    "built_in": True if known_builtin else None,
                })
            continue
        reference = profile.get("applicationReference")
        if reference is not None:
            path = reference.get("link") or endpoint + "/application"
            applications = _waf_collection(client, path,
                                            reference if "items" in reference else None)
        else:
            applications = profile.get("application") or []
        for application in applications:
            if (application.get("remoteStorage", "none") == "none"
                    or not application.get("servers")):
                continue
            resource = _waf_resource(endpoint + "/application", application)
            plan = plan_waf_application(application, wanted, clean,
                                        f"WAF {label} / {application['name']}", resource)
            plan.notes.append(f"remote storage: {application.get('remoteStorage')}; "
                              f"protocol: {application.get('protocol', 'tcp')}")
            plans.append(plan)
    return plans
