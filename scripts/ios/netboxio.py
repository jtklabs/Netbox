"""Talking to NetBox: which devices to check, which standards apply, where results go.

Device selection reuses the chain the discovery work already established rather
than inventing a second one:

    device `poller-<name>` tag  >  site's tag  >  nearest tagged ancestor region

That precedence is not a detail. `scripts/snmp-inventory/snmpinv/selection.py`
resolves it one way (which addresses are mine?) and
`plugins/netbox-discovery/netbox_discovery/resolution.py` resolves it the other
(whose job is this address?), and they have to agree or a device gets onboarded
by a poller that will never visit it again. This is a third reader of the same
rule, so it follows the same precedence, walks regions upwards the same way,
and treats any `poller-` tag that is not ours as somebody else's claim —
structurally, so standing up a new poller never means editing an existing
poller's configuration.

Standards resolution is deliberately NOT reimplemented here. "An empty scope
dimension means no restriction" is a rule with an edge to get wrong, and the
fleet report in NetBox applies it too; asking the API (`?device_id=`) means
there is exactly one implementation and the report and the checker cannot
disagree about what a device is measured against.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

__all__ = ('NetBoxError', 'NetBox', 'poller_tag', 'resolve_device_owners')

POLLER_TAG_PREFIX = 'poller-'
PLUGIN_BASE = '/api/plugins/compliance'


class NetBoxError(RuntimeError):
    pass


class NetBox:
    """A very small NetBox REST client.

    urllib rather than requests, so this half has no dependency at all — the
    only third-party package this tool needs is netmiko, and that is justified
    in the README because SSH is not something to hand-roll. Adding requests on
    top for four GETs and a POST would be a second dependency for no gain.
    """

    def __init__(self, url, token, verify_ssl=True, timeout=30):
        self.base = url.rstrip('/')
        if self.base.endswith('/api'):
            self.base = self.base[:-4]
        self.token = token
        self.timeout = timeout
        self._context = None
        if not verify_ssl:
            import ssl

            self._context = ssl._create_unverified_context()

    # ------------------------------------------------------------------ #
    def _request(self, method, path, payload=None, params=None):
        url = '%s%s' % (self.base, path)
        if params:
            url = '%s?%s' % (url, urllib.parse.urlencode(params, doseq=True))
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header('Authorization', 'Token %s' % self.token)
        request.add_header('Accept', 'application/json')
        if data is not None:
            request.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=self._context
            ) as response:
                body = response.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors='replace')[:400]
            raise NetBoxError('%s %s -> %s %s' % (method, url, exc.code, detail)) from None
        except urllib.error.URLError as exc:
            raise NetBoxError('cannot reach NetBox at %s: %s' % (url, exc.reason)) from None
        return json.loads(body) if body else {}

    def get(self, path, **params):
        return self._request('GET', path, params=params)

    def post(self, path, payload):
        return self._request('POST', path, payload=payload)

    def all(self, path, **params):
        """Every page of a list endpoint.

        `brief` is never passed, not even brief=0: NetBox decides brief mode on
        the parameter being present whatever its value, and a brief region
        carries neither `parent` nor `tags` — which is everything the ownership
        walk needs. That footgun cost the SNMP scanner a debugging session; it
        is written down here so it does not cost another.
        """
        params.setdefault('limit', 200)
        results = []
        page = self.get(path, **params)
        results.extend(page.get('results', []))
        while page.get('next'):
            offset = urllib.parse.parse_qs(
                urllib.parse.urlparse(page['next']).query
            ).get('offset', ['0'])[0]
            params['offset'] = offset
            page = self.get(path, **params)
            results.extend(page.get('results', []))
        return results

    # --- Compliance plugin ------------------------------------------------- #
    def standards_for_device(self, device_id):
        """The standards in force that this device is in scope for.

        Resolved server-side on purpose — see the module docstring.
        """
        return self.all(
            '%s/config-standards/' % PLUGIN_BASE, device_id=device_id, active='true'
        )

    def post_results(self, items):
        return self.post('%s/config-compliance/report/' % PLUGIN_BASE, items)

    # --- DCIM -------------------------------------------------------------- #
    def devices(self, **filters):
        return self.all('/api/dcim/devices/', **filters)

    def sites(self):
        return self.all('/api/dcim/sites/')

    def regions(self):
        return self.all('/api/dcim/regions/')


# --------------------------------------------------------------------------- #
# Ownership: which poller is responsible for a device
# --------------------------------------------------------------------------- #
def poller_tag(name):
    """`boston` -> `poller-boston`. An already-prefixed name is accepted.

    Operators reasonably configure a poller with either spelling, and the SNMP
    scanner accepts both — so accepting only one here would let a poller resolve
    its sites perfectly and still be handed no work.
    """
    name = (name or '').strip().lower()
    if name.startswith(POLLER_TAG_PREFIX):
        return name
    return '%s%s' % (POLLER_TAG_PREFIX, name)


def _claims(tags):
    return [
        (tag.get('slug') or '').lower()
        for tag in tags or []
        if (tag.get('slug') or '').lower().startswith(POLLER_TAG_PREFIX)
    ]


def _pick(claims, our_tag):
    """Our tag wins if present; otherwise the first claim is somebody else's."""
    if our_tag in claims:
        return our_tag
    return claims[0] if claims else None


def resolve_device_owners(netbox, our_poller):
    """Map every device id to the poller that owns it, or None.

    Resolved object by object in Python rather than with tag filters: the rule
    involves inheritance up a region tree, which is not expressible as a
    queryset filter, and NetBox's tag filters AND together rather than OR, so
    the query-side approach needs a request per tag anyway. Regions and sites
    are small tables.
    """
    our_tag = poller_tag(our_poller)

    region_claims = {}
    region_parent = {}
    for region in netbox.regions():
        region_claims[region['id']] = _claims(region.get('tags'))
        parent = region.get('parent')
        region_parent[region['id']] = parent['id'] if parent else None

    def region_owner(region_id):
        seen = set()
        while region_id is not None and region_id not in seen:
            seen.add(region_id)
            owner = _pick(region_claims.get(region_id, []), our_tag)
            if owner:
                return owner
            region_id = region_parent.get(region_id)
        return None

    site_owner = {}
    for site in netbox.sites():
        owner = _pick(_claims(site.get('tags')), our_tag)
        if owner is None:
            region = site.get('region')
            owner = region_owner(region['id']) if region else None
        site_owner[site['id']] = owner

    owners = {}
    for device in netbox.devices():
        owner = _pick(_claims(device.get('tags')), our_tag)
        if owner is None:
            site = device.get('site')
            owner = site_owner.get(site['id']) if site else None
        owners[device['id']] = owner
    return owners, our_tag
