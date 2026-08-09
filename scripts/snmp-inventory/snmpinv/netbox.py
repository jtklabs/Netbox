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
        """POST, honouring dry-run. Returns None when nothing was written."""
        what = label or path
        if self.dry_run:
            log.info("[dry-run] would create %s: %s", what, _brief(payload))
            self.created[path] = self.created.get(path, 0) + 1
            return None
        response = self._request("POST", path, json=payload)
        if response.status_code not in (200, 201):
            raise NetBoxError(f"POST {path} -> {response.status_code}: {response.text[:400]}")
        self.created[path] = self.created.get(path, 0) + 1
        log.info("created %s: %s", what, _brief(payload))
        return response.json()

    def update(self, path: str, object_id: int, payload: dict, label: str = "") -> dict | None:
        what = label or f"{path}{object_id}"
        if self.dry_run:
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
        """Return the existing object matching `lookup`, else create it.

        In dry-run a missing object yields None, and callers must cope with
        that — an object that was never created has no id to reference. Doing it
        this way keeps dry-run honest: it reports exactly the chain of objects a
        real run would have had to create.
        """
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
