"""Thin NetBox REST client: pagination, lookup-or-create, and a dry-run mode.

Everything the scanner writes goes through `ensure()`, which looks an object up
by its natural key before creating it. That is what makes re-running the
scanner safe: NetBox rejects duplicates with a 400 rather than silently making
a second copy, so a create-first strategy would turn every rescan into a wall
of errors.

Dry-run is enforced here rather than at each call site. A single choke point
means a new writer added later cannot forget to honour it.

API facts this client depends on were verified against a live NetBox 4.6.7 —
see docs/API-NOTES.md. Two are worth repeating because they are easy to get
wrong:

  * Filtering by a tag slug that does not exist returns 400, not an empty list.
    Callers must check the tag exists first.
  * MAC addresses are their own model in NetBox 4.x. `interface.mac_address` is
    read-only and duplicate POSTs to /dcim/mac-addresses/ are NOT deduplicated,
    so MACs need an explicit lookup before create.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable, Iterator
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # pragma: no cover - the CLI prints a friendlier message
    raise SystemExit("This tool needs the 'requests' package: pip install -r requirements.txt")

log = logging.getLogger(__name__)

# NetBox's default page size is 50; asking for more cuts round trips on the
# interface-heavy endpoints without risking a timeout on a big instance.
PAGE_SIZE = 250

# Marks an object that only exists because we are in dry-run.
PLACEHOLDER_KEY = "_snmpinv_dry_run"


def _references_placeholder(params: dict | None) -> bool:
    """True if a query filters on the id of a dry-run placeholder object."""
    for value in (params or {}).values():
        if isinstance(value, int) and not isinstance(value, bool) and value < 0:
            return True
        if isinstance(value, str) and value.lstrip("-").isdigit() and int(value) < 0:
            return True
    return False


class NetBoxError(Exception):
    """A NetBox request failed in a way the caller cannot paper over."""


class NetBox:
    def __init__(self, url: str, token: str, verify_ssl: bool = True, timeout: int = 30,
                 dry_run: bool = False, retries: int = 3):
        # The API always lives under <base>/api/. Accepting either form spares
        # operators from guessing whether to include it.
        base = url.rstrip("/")
        if not base.endswith("/api"):
            base += "/api"
        self.base = base + "/"
        self.dry_run = dry_run
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        # NetBox 4.6 v2 tokens authenticate as a bearer token; v1 tokens use
        # the older "Token <key>" scheme. Pick by prefix so both work.
        scheme = "Bearer" if token.startswith("nbt_") else "Token"
        self.session.headers.update({
            "Authorization": f"{scheme} {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self.session.verify = verify_ssl
        self.created: dict[str, int] = {}
        self.updated: dict[str, int] = {}
        # Dry-run stand-ins get descending negative ids. Real NetBox ids are
        # always positive, so a negative id anywhere downstream is
        # unambiguously an object this run only pretended to create.
        self._next_placeholder_id = -1
        self._plugins: dict | None = None
        self._endpoints: dict[str, bool] = {}

    # --- HTTP ---------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = urljoin(self.base, path.lstrip("/"))
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                # A poller talks to NetBox over a WAN and NetBox itself gets
                # restarted; a transient connection error should not abandon a
                # scan that took minutes to collect.
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise NetBoxError(f"{method} {url} failed: {exc}") from exc
            if response.status_code in (502, 503, 504) and attempt + 1 < self.retries:
                time.sleep(2 ** attempt)
                continue
            return response
        raise NetBoxError(f"{method} {url} failed: {last_error}")

    def get(self, path: str, params: dict | None = None) -> dict:
        if _references_placeholder(params):
            # Filtering on the id of an object that was never created would be
            # a 400 from NetBox ("Select a valid choice"), and the answer is
            # knowable without asking: nothing can reference it yet.
            return {"count": 0, "results": [], "next": None, "previous": None}
        response = self._request("GET", path, params=params)
        if not response.ok:
            raise NetBoxError(f"GET {path} {params or ''} -> {response.status_code}: {response.text[:400]}")
        return response.json()

    def paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """Yield every result across pages."""
        query = dict(params or {})
        query.setdefault("limit", PAGE_SIZE)
        offset = 0
        while True:
            query["offset"] = offset
            payload = self.get(path, query)
            results = payload.get("results", [])
            yield from results
            if not payload.get("next") or not results:
                return
            offset += len(results)

    def all(self, path: str, params: dict | None = None) -> list[dict]:
        return list(self.paginate(path, params))

    def first(self, path: str, params: dict | None = None) -> dict | None:
        payload = self.get(path, {**(params or {}), "limit": 1})
        results = payload.get("results", [])
        return results[0] if results else None

    def count(self, path: str, params: dict | None = None) -> int:
        return self.get(path, {**(params or {}), "limit": 1}).get("count", 0)

    # --- writes -------------------------------------------------------------

    def create(self, path: str, payload: dict, label: str = "") -> dict | None:
        """POST, honouring dry-run.

        In dry-run this returns a placeholder object rather than None. That
        matters: without an id to reference, every child of a would-be-created
        object is abandoned, and a dry run of a fresh NetBox reports only the
        handful of top-level objects while silently dropping every device,
        interface and address underneath them — which reads as "the scan found
        nothing" rather than "nothing exists yet". Placeholders let the whole
        chain be reported.
        """
        what = label or path
        if self.dry_run:
            log.info("[dry-run] would create %s: %s", what, _brief(payload))
            self.created[path] = self.created.get(path, 0) + 1
            placeholder = dict(payload)
            placeholder["id"] = self._next_placeholder_id
            placeholder[PLACEHOLDER_KEY] = True
            self._next_placeholder_id -= 1
            return placeholder
        response = self._request("POST", path, json=payload)
        if response.status_code not in (200, 201):
            raise NetBoxError(f"POST {path} -> {response.status_code}: {response.text[:400]}")
        self.created[path] = self.created.get(path, 0) + 1
        log.info("created %s: %s", what, _brief(payload))
        return response.json()

    def update(self, path: str, object_id: int, payload: dict, label: str = "") -> dict | None:
        what = label or f"{path}{object_id}"
        if self.dry_run:
            # An update to something this run only pretended to create is noise:
            # the create already reported those fields.
            if object_id is not None and object_id < 0:
                return None
            log.info("[dry-run] would update %s: %s", what, _brief(payload))
            self.updated[path] = self.updated.get(path, 0) + 1
            return None
        response = self._request("PATCH", f"{path}{object_id}/", json=payload)
        if not response.ok:
            raise NetBoxError(f"PATCH {path}{object_id}/ -> {response.status_code}: {response.text[:400]}")
        self.updated[path] = self.updated.get(path, 0) + 1
        log.info("updated %s: %s", what, _brief(payload))
        return response.json()

    def ensure(self, path: str, lookup: dict, payload: dict, label: str = "") -> dict | None:
        """Return the existing object matching `lookup`, else create it."""
        existing = self.first(path, lookup)
        if existing is not None:
            return existing
        return self.create(path, payload, label=label)

    def ensure_fields(self, path: str, existing: dict, desired: dict, label: str = "") -> dict:
        """Patch only the fields that actually differ.

        Comparing before writing keeps re-runs quiet in NetBox's changelog. A
        scanner that PATCHes unconditionally makes every object look edited on
        every pass, which destroys the changelog's usefulness for spotting real
        drift.
        """
        changes = {}
        for key, value in desired.items():
            if value in (None, ""):
                # Never blank a field we simply failed to collect. An operator's
                # hand-entered value is better than nothing.
                continue
            if _normalise(existing.get(key)) != _normalise(value):
                changes[key] = value
        if not changes:
            return existing
        updated = self.update(path, existing["id"], changes, label=label)
        return updated or existing

    # --- helpers used across the sync layer ---------------------------------

    def plugin_installed(self, name: str) -> bool:
        """Is a NetBox plugin present on this instance? Read once and cached."""
        if self._plugins is None:
            try:
                self._plugins = self.get("/status/").get("plugins", {}) or {}
            except NetBoxError:
                self._plugins = {}
        return name in self._plugins

    def endpoint_available(self, path: str) -> bool:
        """Does this instance actually serve `path`?

        Checking the plugin is installed is not enough. A poller ships on its
        own schedule and may well be pointed at a NetBox running an older
        version of a plugin — one that exists but does not yet have the
        endpoint we want. Probing the endpoint is the only honest test, and it
        makes the scanner degrade to its fallback instead of failing every
        write with an HTML 404 page.
        """
        if path in self._endpoints:
            return self._endpoints[path]
        try:
            response = self._request("GET", path, params={"limit": 1})
            available = response.status_code == 200
        except NetBoxError:
            available = False
        self._endpoints[path] = available
        return available

    def post_raw(self, path: str, payload, label: str = "") -> dict | None:
        """POST an arbitrary payload, honouring dry-run.

        Used for plugin endpoints that are not plain object creates, so they do
        not get counted as created objects.
        """
        if self.dry_run:
            count = len(payload) if isinstance(payload, list) else 1
            log.info("[dry-run] would post %d item(s) to %s", count, label or path)
            return None
        response = self._request("POST", path, json=payload)
        if not response.ok:
            raise NetBoxError(f"POST {path} -> {response.status_code}: {response.text[:400]}")
        try:
            return response.json()
        except ValueError:
            return None

    def tag_exists(self, slug: str) -> bool:
        """Check a tag exists before filtering by it.

        NetBox returns 400 for `?tag=<unknown-slug>` rather than an empty list,
        so every tag-filtered query has to be guarded by this.
        """
        return self.count("/extras/tags/", {"slug": slug}) > 0

    def ensure_tag(self, slug: str, name: str = "") -> dict | None:
        return self.ensure(
            "/extras/tags/",
            {"slug": slug},
            {"slug": slug, "name": name or slug},
            label=f"tag {slug}",
        )

    def ensure_custom_field(self, name: str, object_types: Iterable[str], field_type: str = "text",
                            label: str = "", description: str = "") -> dict | None:
        """Create a custom field if the instance does not already have one.

        NetBox has no per-device software version field of its own, so the
        version we collect needs somewhere to live. Creating it here means a
        fresh poller against a fresh NetBox works with no manual setup.
        """
        existing = self.first("/extras/custom-fields/", {"name": name})
        if existing is not None:
            return existing
        return self.create(
            "/extras/custom-fields/",
            {
                "name": name,
                "label": label or name.replace("_", " ").title(),
                "type": field_type,
                "object_types": list(object_types),
                "description": description,
                "required": False,
            },
            label=f"custom field {name}",
        )

    def summary(self) -> str:
        if not self.created and not self.updated:
            return "no changes"
        parts = []
        for path, n in sorted(self.created.items()):
            parts.append(f"+{n} {path.strip('/').split('/')[-1]}")
        for path, n in sorted(self.updated.items()):
            parts.append(f"~{n} {path.strip('/').split('/')[-1]}")
        return ", ".join(parts)


def _normalise(value: Any) -> Any:
    """Compare API-shaped values against the plain values we send.

    NetBox returns nested objects and choice dicts where it accepts bare ids and
    slugs, so a naive comparison would report a difference on every field and
    PATCH forever.
    """
    if isinstance(value, dict):
        if "value" in value:            # choice field: {"value": "active", ...}
            return value["value"]
        if "id" in value:               # related object
            return value["id"]
    if isinstance(value, str):
        return value.strip()
    return value


def _brief(payload: dict) -> str:
    """A short, log-friendly rendering that never prints a whole interface list."""
    interesting = ("name", "model", "slug", "address", "serial", "device", "mac_address")
    bits = [f"{k}={payload[k]}" for k in interesting if k in payload]
    return " ".join(bits) or str(sorted(payload))[:120]
