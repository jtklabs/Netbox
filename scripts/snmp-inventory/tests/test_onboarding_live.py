"""The onboarding flow end to end, against a real NetBox with the plugin.

Someone types an IP; a poller picks it up, reports what it found, and writes
nothing; a person approves; the poller creates the device. This walks that
whole path.

Scan results come from recorded walks rather than an emulated device — what is
under test is the queue and the review gate, not the wire. The SNMP path is
covered in test_emulated.py, and keeping them apart means this runs anywhere
there is a NetBox rather than only where net-snmp is new enough.

Opt in the same way as test_netbox_live.py:

    export SNMPINV_TEST_NETBOX_URL=http://localhost:8080/netbox
    export SNMPINV_TEST_NETBOX_TOKEN=nbt_...
"""

from __future__ import annotations

import os

import pytest
from conftest import collect_fixture

from snmpinv import onboarding
from snmpinv.model import build_scan_result
from snmpinv.netbox import NetBox, NetBoxError
from snmpinv.sync import Syncer, SyncOptions

URL = os.environ.get("SNMPINV_TEST_NETBOX_URL", "")
TOKEN = os.environ.get("SNMPINV_TEST_NETBOX_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not (URL and TOKEN),
    reason="set SNMPINV_TEST_NETBOX_URL and SNMPINV_TEST_NETBOX_TOKEN to run",
)

PREFIX = "ONBOARD_TEST_"
SLUG = "onboard-test-"
POLLER = "onboard-test-boston"
OUR_TAG = "poller-%s" % POLLER
TEST_PREFIX = "192.0.2.0/24"
TEST_ADDRESS = "192.0.2.50"

REQUESTS = "/plugins/discovery/onboarding-requests/"


class ReplayCollector:
    """Stands in for the SNMP collector, answering from a recorded walk."""

    def __init__(self, fixture="cisco-c9300-stack"):
        self.fixture = fixture
        self.calls = []

    def collect(self, host):
        self.calls.append(host)
        return collect_fixture(self.fixture, host=host)


@pytest.fixture(scope="module")
def netbox():
    box = NetBox(URL, TOKEN, verify_ssl=False, timeout=30)
    if not onboarding.plugin_available(box):
        pytest.skip("the Discovery plugin is not installed on this NetBox")
    return box


@pytest.fixture(scope="module")
def lab(netbox):
    """A tagged region, a site under it, and a prefix scoped to that site."""
    created = []

    def make(path, lookup, payload):
        existing = netbox.first(path, lookup)
        if existing is not None:
            fixup = {k: v for k, v in payload.items()
                     if k in ('region', 'tags', 'scope_type', 'scope_id')}
            if fixup:
                netbox.update(path, existing["id"], fixup)
                existing = netbox.first(path, lookup)
            return existing
        obj = netbox.create(path, payload)
        created.append((path, obj["id"]))
        return obj

    make("/extras/tags/", {"slug": OUR_TAG}, {"name": OUR_TAG, "slug": OUR_TAG})
    region = make("/dcim/regions/", {"slug": SLUG + "ne"},
                  {"name": PREFIX + "Northeast", "slug": SLUG + "ne",
                   "tags": [{"slug": OUR_TAG}]})
    site = make("/dcim/sites/", {"slug": SLUG + "dc1"},
                {"name": PREFIX + "DC1", "slug": SLUG + "dc1", "region": region["id"]})
    make("/ipam/prefixes/", {"prefix": TEST_PREFIX},
         {"prefix": TEST_PREFIX, "scope_type": "dcim.site", "scope_id": site["id"]})

    yield {"region": region, "site": site}

    _teardown(netbox)


def _teardown(netbox):
    import requests

    session = netbox.session

    def delete(path, oid):
        try:
            session.delete("%s%s%s/" % (netbox.base, path.lstrip("/"), oid), timeout=30)
        except requests.RequestException:
            pass

    for entry in netbox.all(REQUESTS):
        if str(entry.get("address", "")).startswith(("192.0.2.", "198.18.", "198.19.")):
            delete(REQUESTS, entry["id"])

    sites = [s for s in netbox.all("/dcim/sites/")
             if (s.get("slug") or "").startswith(SLUG)]
    for site in sites:
        for device in netbox.all("/dcim/devices/", {"site_id": site["id"]}):
            delete("/dcim/devices/", device["id"])
    for vc in netbox.all("/dcim/virtual-chassis/"):
        if vc.get("member_count", 0) == 0:
            delete("/dcim/virtual-chassis/", vc["id"])
    for ip in netbox.all("/ipam/ip-addresses/"):
        if str(ip.get("address", "")).startswith(("192.0.2.", "10.10.1.")):
            delete("/ipam/ip-addresses/", ip["id"])
    for pfx in netbox.all("/ipam/prefixes/"):
        if pfx.get("prefix") in (TEST_PREFIX, "198.18.0.0/24"):
            delete("/ipam/prefixes/", pfx["id"])
    for site in sites:
        delete("/dcim/sites/", site["id"])
    for region in netbox.all("/dcim/regions/"):
        if (region.get("slug") or "").startswith(SLUG):
            delete("/dcim/regions/", region["id"])
    for poller in netbox.all("/plugins/discovery/pollers/"):
        if poller.get("name") == POLLER:
            delete("/plugins/discovery/pollers/", poller["id"])
    for vrf in netbox.all("/ipam/vrfs/"):
        if (vrf.get("name") or "").startswith(PREFIX):
            delete("/ipam/vrfs/", vrf["id"])
    for tenant in netbox.all("/tenancy/tenants/"):
        if (tenant.get("slug") or "").startswith(SLUG):
            delete("/tenancy/tenants/", tenant["id"])
    for group in netbox.all("/tenancy/tenant-groups/"):
        if (group.get("slug") or "").startswith(SLUG):
            delete("/tenancy/tenant-groups/", group["id"])
    for tag in netbox.all("/extras/tags/"):
        if tag.get("slug") == OUR_TAG:
            delete("/extras/tags/", tag["id"])


def submit(netbox, address=TEST_ADDRESS):
    return netbox.create(REQUESTS, {"address": address})


def refresh(netbox, request_id):
    return netbox.get("%s%s/" % (REQUESTS, request_id))


def approve(netbox, request_id, **overrides):
    """Approve over the API — the same call the UI button ends up making."""
    return netbox.post_raw(
        "%s%s/approve/" % (REQUESTS, request_id), overrides, label="approve"
    )


def reject(netbox, request_id, reason=""):
    return netbox.post_raw(
        "%s%s/reject/" % (REQUESTS, request_id), {"reason": reason}, label="reject"
    )


class TestResolution:
    def test_address_resolves_to_site_and_poller_with_no_other_input(self, netbox, lab):
        """The whole premise: an address is enough."""
        entry = submit(netbox)
        assert entry["status"] == "pending"
        assert (entry["site"] or {}).get("name") == PREFIX + "DC1"
        assert (entry["poller"] or {}).get("name") == POLLER
        netbox.session.delete("%s%s%s/" % (netbox.base, REQUESTS.lstrip("/"), entry["id"]))

    def test_address_outside_every_prefix_is_refused_with_a_reason(self, netbox, lab):
        """Refusing at submit time is the point — accepting it would mean a
        request nothing ever services, with nothing to explain why."""
        with pytest.raises(NetBoxError) as exc:
            submit(netbox, "203.0.113.99")
        message = str(exc.value)
        assert "400" in message
        assert "No prefix" in message and "203.0.113.99" in message


@pytest.fixture(scope="module")
def flow(netbox, lab, us_region):
    """One request carried through the staged flow by the tests below, in order.

    Deliberately an address no prefix claims. Under the exceptions policy a
    clean scan applies itself, so the scan -> review -> approve -> apply
    sequence only happens for a request that genuinely needs a person — and
    "no site" is the commonest such case.
    """
    return {
        "request": submit(netbox, "198.19.66.5"),
        "collector": ReplayCollector(),
        "syncer": Syncer(netbox, SyncOptions(device_role=SLUG + "network")),
    }


class TestOnboardingFlow:

    def test_poller_receives_the_job(self, netbox, flow):
        jobs = onboarding.check_in(netbox, POLLER, version="test")
        mine = [j for j in jobs if j["id"] == flow["request"]["id"]]
        assert mine, "the poller was not offered its own request"
        assert mine[0]["action"] == "scan"
        assert mine[0]["address"] == "198.19.66.5"
        flow["jobs"] = mine

    def test_another_poller_is_offered_nothing(self, netbox, flow):
        assert onboarding.check_in(netbox, "onboard-test-dallas") == []

    def test_scan_reports_a_preview_and_writes_nothing(self, netbox, flow):
        before = netbox.count("/dcim/devices/")
        counts = onboarding.run_jobs(
            netbox, flow["collector"], flow["syncer"], flow["jobs"]
        )
        assert counts.get("scanned") == 1
        assert netbox.count("/dcim/devices/") == before, (
            "a scan must not create devices — that is what review is for"
        )

        entry = refresh(netbox, flow["request"]["id"])
        assert entry["status"] == "review"
        devices = entry["discovered"]["devices"]
        assert len(devices) == 3, "the stack's three members should be previewed"
        assert {d["serial"] for d in devices} == {
            "FOC2530L0AB", "FOC2530L0CD", "FOC2531L0EF"
        }
        assert devices[0]["model"] == "C9300-48P"

    def test_a_reviewed_request_is_not_handed_out_for_scanning_again(self, netbox, flow):
        jobs = onboarding.check_in(netbox, POLLER)
        assert [j for j in jobs if j["id"] == flow["request"]["id"]] == []

    def test_approval_turns_it_into_an_apply_job(self, netbox, lab, flow):
        # No prefix placed this address, so a site has to be supplied here.
        approve(netbox, flow["request"]["id"], override_site=lab["site"]["id"])
        jobs = onboarding.check_in(netbox, POLLER)
        mine = [j for j in jobs if j["id"] == flow["request"]["id"]]
        assert mine and mine[0]["action"] == "apply"
        flow["apply_jobs"] = mine

    def test_apply_creates_the_device(self, netbox, flow):
        counts = onboarding.run_jobs(
            netbox, flow["collector"], flow["syncer"], flow["apply_jobs"]
        )
        assert counts.get("applied") == 1

        entry = refresh(netbox, flow["request"]["id"])
        assert entry["status"] == "applied"
        assert entry["device"], "the request should name the device it produced"

        device = netbox.get("/dcim/devices/%s/" % entry["device"]["id"])
        assert device["serial"] == "FOC2530L0AB"
        assert device["device_type"]["model"] == "C9300-48P"
        assert device["site"]["name"] == PREFIX + "DC1"

    def test_the_whole_stack_was_created(self, netbox, flow):
        entry = refresh(netbox, flow["request"]["id"])
        device = netbox.get("/dcim/devices/%s/" % entry["device"]["id"])
        vc = device.get("virtual_chassis")
        assert vc, "a stack should have produced a virtual chassis"
        members = netbox.all("/dcim/devices/", {"virtual_chassis_id": vc["id"]})
        assert len(members) == 3
        assert sorted(m["vc_position"] for m in members) == [1, 2, 3]

    def test_a_finished_request_is_not_offered_again(self, netbox, flow):
        jobs = onboarding.check_in(netbox, POLLER)
        assert [j for j in jobs if j["id"] == flow["request"]["id"]] == []


class TestScanFailuresAreReported:
    def test_unreachable_device_lands_as_failed_with_advice(self, netbox, lab):
        from snmpinv.snmp import SnmpTimeoutError

        class Dead:
            def collect(self, host):
                raise SnmpTimeoutError("%s: no response" % host)

        entry = submit(netbox, "192.0.2.61")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        assert jobs
        counts = onboarding.run_jobs(netbox, Dead(), None, jobs)
        assert counts.get("unreachable") == 1

        after = refresh(netbox, entry["id"])
        assert after["status"] == "failed"
        # The message has to be actionable — "failed" alone sends someone
        # hunting through poller logs.
        assert "reachable from this poller" in after["error"]

    def test_rejected_credentials_are_distinguished_from_unreachable(self, netbox, lab):
        from snmpinv.snmp import SnmpAuthError

        class Rejecting:
            def collect(self, host):
                raise SnmpAuthError("%s: no credential accepted" % host)

        entry = submit(netbox, "192.0.2.62")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        counts = onboarding.run_jobs(netbox, Rejecting(), None, jobs)
        assert counts.get("auth-failed") == 1
        after = refresh(netbox, entry["id"])
        assert after["status"] == "failed"
        assert "credential" in after["error"]


class TestHardwareChangeGuard:
    def test_a_swapped_device_is_not_applied(self, netbox, lab):
        """The operator approved a specific box.

        If the serial has changed by the time it is applied, the address now
        points at different hardware and applying would create something
        nobody agreed to.
        """
        entry = submit(netbox, "192.0.2.70")
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))

        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        onboarding.run_jobs(netbox, ReplayCollector("cisco-c9300-stack"), syncer, jobs)
        assert refresh(netbox, entry["id"])["status"] == "review"

        approve(netbox, entry["id"])
        apply_jobs = [j for j in onboarding.check_in(netbox, POLLER)
                      if j["id"] == entry["id"]]
        assert apply_jobs and apply_jobs[0]["action"] == "apply"

        # Same address, different hardware behind it.
        counts = onboarding.run_jobs(
            netbox, ReplayCollector("arista-7050sx"), syncer, apply_jobs
        )
        assert counts.get("changed") == 1

        after = refresh(netbox, entry["id"])
        assert after["status"] == "review", "it must go back for another look"
        assert "hardware changed" in after["error"].lower()
        assert "JPE17240001" in after["error"] or "DCS-7050SX-72Q" in after["error"]


class TestWorkflowOverApi:
    """The whole add -> review -> approve path with no UI involved.

    This is the contract anything automating onboarding depends on, and it must
    behave exactly as the buttons do — both call the same functions, and these
    tests are what keeps that true.
    """

    def test_add_and_scan_end_to_end(self, netbox, lab):
        """The clean path over the API: add an address, and it becomes a device.

        Under the exceptions policy a scan with nothing questionable about it
        does not stop for a person, so there is no approve step here — that is
        covered below, on a request that genuinely needs one.
        """
        entry = submit(netbox, "192.0.2.80")
        assert entry["status"] == "pending"

        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        assert jobs and jobs[0]["action"] == "scan"

        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        counts = onboarding.run_jobs(netbox, ReplayCollector("arista-7050sx"),
                                     syncer, jobs)
        assert counts.get("applied") == 1

        final = refresh(netbox, entry["id"])
        assert final["status"] == "applied"
        assert final["discovered"]["devices"][0]["model"] == "DCS-7050SX-72Q"
        device = netbox.get("/dcim/devices/%s/" % final["device"]["id"])
        assert device["serial"] == "JPE17240001"

    def test_approve_can_override_name_and_site(self, netbox, lab, us_region):
        """Approving with overrides, on a request that really does need review.

        An address no prefix claims has no site, so it stops for a person — and
        the person supplies both the site and the name in the approve call.
        """
        entry = submit(netbox, "198.19.77.10")
        assert entry["used_default_region"] is True
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("checkpoint-gaia"), syncer, jobs)
        assert refresh(netbox, entry["id"])["status"] == "review"

        approved = approve(netbox, entry["id"], override_name="renamed-by-api",
                           override_site=lab["site"]["id"])
        assert approved["status"] == "approved"
        assert approved["override_name"] == "renamed-by-api"

        apply_jobs = [j for j in onboarding.check_in(netbox, POLLER)
                      if j["id"] == entry["id"]]
        assert apply_jobs[0]["override_name"] == "renamed-by-api"
        onboarding.run_jobs(netbox, ReplayCollector("checkpoint-gaia"), syncer, apply_jobs)

        final = refresh(netbox, entry["id"])
        assert final["status"] == "applied"
        device = netbox.get("/dcim/devices/%s/" % final["device"]["id"])
        assert device["name"] == "renamed-by-api", (
            "the override should name the created device, not the discovered hostname"
        )

    def test_approving_something_not_awaiting_review_is_refused(self, netbox, lab):
        """A caller must not be able to skip the scan by approving early."""
        entry = submit(netbox, "192.0.2.82")
        with pytest.raises(NetBoxError) as exc:
            approve(netbox, entry["id"])
        assert "409" in str(exc.value)
        assert "awaiting review" in str(exc.value)
        assert refresh(netbox, entry["id"])["status"] == "pending"

    def test_reject_over_api_records_the_reason(self, netbox, lab, us_region):
        entry = submit(netbox, "198.19.77.11")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("infoblox-nios"), syncer, jobs)
        assert refresh(netbox, entry["id"])["status"] == "review"

        rejected = reject(netbox, entry["id"], reason="decommissioned, do not onboard")
        assert rejected["status"] == "rejected"
        assert "decommissioned" in rejected["error"]

        # And a rejected request is never handed to a poller again.
        assert [j for j in onboarding.check_in(netbox, POLLER)
                if j["id"] == entry["id"]] == []

    def test_retry_picks_up_a_prefix_created_after_the_fact(self, netbox, lab):
        """The common fix: the address was submitted before IPAM knew about it."""
        entry = submit(netbox, "192.0.2.84")
        # Force it into the failed state a dead device would produce.
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]

        class Dead:
            def collect(self, host):
                from snmpinv.snmp import SnmpTimeoutError
                raise SnmpTimeoutError("%s: no response" % host)

        onboarding.run_jobs(netbox, Dead(), None, jobs)
        assert refresh(netbox, entry["id"])["status"] == "failed"

        retried = netbox.post_raw("%s%s/retry/" % (REQUESTS, entry["id"]), {},
                                  label="retry")
        assert retried["status"] == "pending"
        assert retried["error"] == ""
        assert [j for j in onboarding.check_in(netbox, POLLER)
                if j["id"] == entry["id"]], "a retried request should be offered again"


@pytest.fixture(scope="module")
def overlapping(netbox, lab):
    """The same prefix twice, owned by two different tenants."""
    def make(path, lookup, payload):
        found = netbox.first(path, lookup)
        return found if found else netbox.create(path, payload)

    group = make("/tenancy/tenant-groups/", {"slug": SLUG + "acq"},
                 {"name": PREFIX + "Acquisitions", "slug": SLUG + "acq"})
    alpha = make("/tenancy/tenants/", {"slug": SLUG + "alpha"},
                 {"name": PREFIX + "Alpha", "slug": SLUG + "alpha", "group": group["id"]})
    beta = make("/tenancy/tenants/", {"slug": SLUG + "beta"},
                {"name": PREFIX + "Beta", "slug": SLUG + "beta", "group": group["id"]})
    site_a = make("/dcim/sites/", {"slug": SLUG + "alpha-hq"},
                  {"name": PREFIX + "Alpha HQ", "slug": SLUG + "alpha-hq",
                   "tags": [{"slug": OUR_TAG}]})
    site_b = make("/dcim/sites/", {"slug": SLUG + "beta-hq"},
                  {"name": PREFIX + "Beta HQ", "slug": SLUG + "beta-hq",
                   "tags": [{"slug": OUR_TAG}]})
    # Overlapping space lives in VRFs, not in the global table: with
    # ENFORCE_GLOBAL_UNIQUE on (the NetBox default) the second identical
    # prefix in the global table is refused outright, whatever its tenant.
    vrf_a = make("/ipam/vrfs/", {"name": PREFIX + "ALPHA"},
                 {"name": PREFIX + "ALPHA", "tenant": alpha["id"]})
    vrf_b = make("/ipam/vrfs/", {"name": PREFIX + "BETA"},
                 {"name": PREFIX + "BETA", "tenant": beta["id"]})
    if netbox.count("/ipam/prefixes/", {"prefix": "198.18.0.0/24"}) < 2:
        netbox.create("/ipam/prefixes/",
                      {"prefix": "198.18.0.0/24", "tenant": alpha["id"],
                       "vrf": vrf_a["id"],
                       "scope_type": "dcim.site", "scope_id": site_a["id"]})
        netbox.create("/ipam/prefixes/",
                      {"prefix": "198.18.0.0/24", "tenant": beta["id"],
                       "vrf": vrf_b["id"],
                       "scope_type": "dcim.site", "scope_id": site_b["id"]})
    return {"alpha": alpha, "beta": beta, "group": group,
            "site_a": site_a, "site_b": site_b,
            "vrf_a": vrf_a, "vrf_b": vrf_b}


@pytest.fixture(scope="module")
def us_region(netbox, lab):
    """The configured default region, tagged for our poller."""
    region = netbox.first("/dcim/regions/", {"slug": "us"})
    if region is None:
        region = netbox.create("/dcim/regions/",
                               {"name": "US", "slug": "us", "tags": [{"slug": OUR_TAG}]})
    else:
        netbox.update("/dcim/regions/", region["id"], {"tags": [{"slug": OUR_TAG}]})
    return region


class TestOverlappingAddressSpace:
    """Duplicate space across acquired companies.

    NetBox does not enforce prefix uniqueness in the global table, so the same
    /24 can exist twice with different tenants and a containment lookup returns
    both. Choosing by mask length alone would be a coin toss that files an
    acquired company's switch under our site.
    """

    def test_the_global_table_refuses_a_duplicate(self, netbox, overlapping):
        """Tenant is not a namespace — this is the constraint that forces VRFs.

        With ENFORCE_GLOBAL_UNIQUE on (NetBox's default) the same prefix cannot
        sit in the global table twice however its tenants differ, so overlapping
        space between acquired companies has to be held in separate VRFs.

        Uses a prefix of its own: duplicate detection is scoped per VRF, so a
        global entry would not collide with the VRF-scoped ones above and would
        instead quietly make every other address here ambiguous.
        """
        first = netbox.create("/ipam/prefixes/",
                              {"prefix": "198.18.9.0/24",
                               "tenant": overlapping["alpha"]["id"]})
        try:
            with pytest.raises(NetBoxError) as exc:
                netbox.create("/ipam/prefixes/",
                              {"prefix": "198.18.9.0/24",
                               "tenant": overlapping["beta"]["id"]})
            assert "Duplicate prefix found in global table" in str(exc.value)
        finally:
            netbox.session.delete(
                "%sipam/prefixes/%s/" % (netbox.base, first["id"]), timeout=30
            )

    def test_the_same_prefix_exists_twice_across_vrfs(self, netbox, overlapping):
        assert netbox.count("/ipam/prefixes/", {"prefix": "198.18.0.0/24"}) == 2

    def test_ambiguous_address_is_refused_not_guessed(self, netbox, overlapping):
        with pytest.raises(NetBoxError) as exc:
            submit(netbox, "198.18.0.10")
        message = str(exc.value)
        assert "different owners" in message
        assert PREFIX + "Alpha" in message and PREFIX + "Beta" in message

    def test_tenant_resolves_it_to_the_right_site(self, netbox, overlapping):
        entry = netbox.create(REQUESTS, {"address": "198.18.0.11",
                                         "tenant": overlapping["alpha"]["id"]})
        assert entry["status"] == "pending"
        assert entry["site"]["name"] == PREFIX + "Alpha HQ"
        assert entry["tenant"]["name"] == PREFIX + "Alpha"

    def test_the_other_tenant_gets_the_other_site(self, netbox, overlapping):
        entry = netbox.create(REQUESTS, {"address": "198.18.0.12",
                                         "tenant": overlapping["beta"]["id"]})
        assert entry["site"]["name"] == PREFIX + "Beta HQ", (
            "the same address under a different tenant must land at a different site"
        )

    def test_vrf_alone_also_disambiguates(self, netbox, overlapping):
        """VRF is the mechanism NetBox actually provides for this, so it has to
        work on its own — not everyone labels tenants."""
        entry = netbox.create(REQUESTS, {"address": "198.18.0.14",
                                         "vrf": overlapping["vrf_b"]["id"]})
        assert entry["site"]["name"] == PREFIX + "Beta HQ"
        # And the tenant is inherited from the prefix, so ownership still lands.
        assert entry["tenant"]["name"] == PREFIX + "Beta"

    def test_tenant_is_stamped_onto_the_created_device(self, netbox, overlapping):
        """Ownership has to survive onboarding, not just route it."""
        entry = netbox.create(REQUESTS, {"address": "198.18.0.13",
                                         "tenant": overlapping["beta"]["id"]})
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        assert jobs and jobs[0]["tenant"] == overlapping["beta"]["id"]

        # A clean scan applies itself, so there is no approve step here.
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("fortigate-600e"), syncer, jobs)

        final = refresh(netbox, entry["id"])
        assert final["status"] == "applied"
        device = netbox.get("/dcim/devices/%s/" % final["device"]["id"])
        assert (device.get("tenant") or {}).get("name") == PREFIX + "Beta"


class TestDefaultRegionFallback:
    """An address no prefix claims still gets scanned, but cannot be applied
    until somebody says where it lives."""

    def test_unmatched_address_falls_back_to_the_default_region(self, netbox, us_region):
        entry = submit(netbox, "198.19.55.5")
        assert entry["status"] == "pending"
        assert entry["used_default_region"] is True
        assert (entry["poller"] or {}).get("name") == POLLER
        assert entry["site"] is None, "there is no prefix, so there is no site"

    def test_it_can_be_scanned_but_not_applied_without_a_site(self, netbox, us_region):
        entry = submit(netbox, "198.19.55.6")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        assert jobs and jobs[0]["action"] == "scan"
        assert jobs[0]["site"] is None

        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("cisco-2960x"), syncer, jobs)
        assert refresh(netbox, entry["id"])["status"] == "review"

        # Approving without a site must be refused — that is what stops a
        # fallback device landing somewhere arbitrary.
        with pytest.raises(NetBoxError) as exc:
            approve(netbox, entry["id"])
        assert "409" in str(exc.value)
        assert "no site" in str(exc.value).lower()

    def test_approving_with_a_site_override_works(self, netbox, lab, us_region):
        entry = submit(netbox, "198.19.55.7")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("cisco-2960x"), syncer, jobs)

        approved = approve(netbox, entry["id"], override_site=lab["site"]["id"])
        assert approved["status"] == "approved"
        assert approved["override_site"]["id"] == lab["site"]["id"]


class TestTheReviewedReadingIsSufficient:
    """What is stored at review has to be enough to apply from.

    Apply normally re-reads the device, which is more correct — but a switch
    that happens to be rebooting must not force somebody to start over. That
    only works if the stored preview kept the addresses, MACs and speeds, not
    just a summary.
    """

    def test_the_preview_keeps_what_a_sync_needs(self, netbox, lab):
        entry = submit(netbox, "192.0.2.91")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("cisco-c9300-stack"), syncer, jobs)

        stored = refresh(netbox, entry["id"])["discovered"]
        interfaces = stored["devices"][0]["interfaces"]
        assert interfaces, "no interfaces were stored"
        first = interfaces[0]
        for field in ("name", "type", "mac_address", "mtu", "speed_kbps",
                      "description", "enabled", "ip_addresses"):
            assert field in first, "%s is missing from the stored preview" % field

        # The management address in particular — losing it would mean an
        # applied-from-preview device had no IP at all.
        addresses = [ip for d in stored["devices"] for i in d["interfaces"]
                     for ip in (i.get("ip_addresses") or [])]
        assert "10.10.1.5/24" in addresses

    def test_apply_falls_back_to_the_reviewed_reading_when_unreachable(
            self, netbox, lab, us_region):
        from snmpinv.snmp import SnmpTimeoutError

        # An address with no prefix, so it stops for review and there is a real
        # gap between the reading and the apply.
        entry = submit(netbox, "198.19.66.6")
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        onboarding.run_jobs(netbox, ReplayCollector("cisco-2960x"), syncer, jobs)
        approve(netbox, entry["id"], override_site=lab["site"]["id"])

        class Unreachable:
            def collect(self, host):
                raise SnmpTimeoutError("%s: no response" % host)

        apply_jobs = [j for j in onboarding.check_in(netbox, POLLER)
                      if j["id"] == entry["id"]]
        counts = onboarding.run_jobs(netbox, Unreachable(), syncer, apply_jobs)
        assert counts.get("applied") == 1, (
            "an approved request should still apply from the reviewed reading"
        )

        final = refresh(netbox, entry["id"])
        assert final["status"] == "applied"
        device = netbox.get("/dcim/devices/%s/" % final["device"]["id"])
        assert device["serial"] == "FOC1934X0AB"
        # And it was built properly, not as a stub.
        interfaces = netbox.all("/dcim/interfaces/", {"device_id": device["id"]})
        assert len(interfaces) == 4
        assert any(i["name"] == "Vlan1" for i in interfaces)

    def test_a_request_with_no_stored_scan_still_fails_honestly(self, netbox, lab):
        """The fallback must not turn an unscanned request into a device."""
        from snmpinv.snmp import SnmpTimeoutError

        entry = submit(netbox, "192.0.2.93")

        class Unreachable:
            def collect(self, host):
                raise SnmpTimeoutError("%s: no response" % host)

        job = {"id": entry["id"], "address": "192.0.2.93", "action": "apply",
               "site": lab["site"]["id"], "site_name": "", "override_name": "",
               "role": "", "tenant": None, "tenant_name": ""}
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        counts = onboarding.run_jobs(netbox, Unreachable(), syncer, [job])
        assert counts.get("unreachable") == 1
        after = refresh(netbox, entry["id"])
        assert "no stored scan to fall back on" in after["error"]


class TestReviewOnlyOnExceptions:
    """A clean scan should not need a person; a questionable one should.

    Reviewing everything sounds safer and is not — it teaches people to click
    Apply without reading, which is worse than not asking.
    """

    def test_a_clean_scan_applies_itself(self, netbox, lab):
        entry = submit(netbox, "192.0.2.101")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        counts = onboarding.run_jobs(netbox, ReplayCollector("f5-bigip"), syncer, jobs)

        assert counts.get("applied") == 1, "a clean scan should not stop at review"
        after = refresh(netbox, entry["id"])
        assert after["status"] == "applied"
        assert after["device"], "the device should exist already"
        device = netbox.get("/dcim/devices/%s/" % after["device"]["id"])
        assert device["serial"] == "f5-chs-01234567"

    def test_it_took_one_check_in_not_two(self, netbox, lab):
        """Applying happens in the same run as the scan, using the reading
        already in hand — no second walk, no waiting for the next tick."""
        entry = submit(netbox, "192.0.2.102")
        collector = ReplayCollector("palo-pa3220")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        onboarding.run_jobs(
            netbox, collector, Syncer(netbox, SyncOptions(device_role=SLUG + "network")),
            jobs,
        )
        assert refresh(netbox, entry["id"])["status"] == "applied"
        assert len(collector.calls) == 1, (
            "the device should have been walked once, not once to scan and again to apply"
        )

    def test_a_device_with_no_model_still_waits_for_a_person(self, netbox, lab):
        class NoModel:
            def collect(self, host):
                facts = collect_fixture("cisco-2960x", host=host)
                for entity in facts.entities:
                    entity.model = ""
                facts.vendor_model = ""
                return facts

        entry = submit(netbox, "192.0.2.103")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        onboarding.run_jobs(netbox, NoModel(),
                            Syncer(netbox, SyncOptions(device_role=SLUG + "network")), jobs)

        after = refresh(netbox, entry["id"])
        assert after["status"] == "review"
        assert "did not report a model" in after["error"]

    def test_a_serial_already_in_netbox_waits_for_a_person(self, netbox, lab):
        """Two addresses reaching the same box, or a mistyped serial. Either
        way creating a second device for existing hardware is how an inventory
        stops being trusted."""
        first = submit(netbox, "192.0.2.104")
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == first["id"]]
        onboarding.run_jobs(netbox, ReplayCollector("juniper-ex4300"), syncer, jobs)
        assert refresh(netbox, first["id"])["status"] == "applied"

        # The same physical box turns up again on another address.
        second = submit(netbox, "192.0.2.105")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == second["id"]]
        onboarding.run_jobs(netbox, ReplayCollector("juniper-ex4300"), syncer, jobs)

        after = refresh(netbox, second["id"])
        assert after["status"] == "review"
        assert "already on" in after["error"]
        assert "PE3714AF0123" in after["error"]


class TestManualEntry:
    """Devices SNMP cannot reach still belong in the inventory."""

    def test_a_failed_scan_can_be_completed_by_hand(self, netbox, lab):
        from snmpinv.snmp import SnmpTimeoutError

        class NoSnmp:
            def collect(self, host):
                raise SnmpTimeoutError("%s: no response" % host)

        entry = submit(netbox, "192.0.2.110")
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        onboarding.run_jobs(netbox, NoSnmp(), syncer, jobs)
        assert refresh(netbox, entry["id"])["status"] == "failed"

        manufacturer = netbox.first("/dcim/manufacturers/", {"slug": "cisco"}) or \
            netbox.create("/dcim/manufacturers/", {"name": "Cisco", "slug": "cisco"})
        recorded = netbox.post_raw(
            "%s%s/manual/" % (REQUESTS, entry["id"]),
            {
                "name": "no-snmp-switch-01",
                "manufacturer": manufacturer["name"],
                "model": "WS-C2960-24TT-L",
                "serial": "HANDTYPED0001",
                "software_version": "12.2(55)SE",
            },
            label="manual entry",
        )
        assert recorded["status"] == "approved"
        assert recorded["manually_entered"] is True

        # It is created by the same apply path — the device is unreachable, so
        # the poller falls back to what was recorded.
        apply_jobs = [j for j in onboarding.check_in(netbox, POLLER)
                      if j["id"] == entry["id"]]
        assert apply_jobs and apply_jobs[0]["action"] == "apply"
        counts = onboarding.run_jobs(netbox, NoSnmp(), syncer, apply_jobs)
        assert counts.get("applied") == 1

        final = refresh(netbox, entry["id"])
        assert final["status"] == "applied"
        device = netbox.get("/dcim/devices/%s/" % final["device"]["id"])
        assert device["name"] == "no-snmp-switch-01"
        assert device["serial"] == "HANDTYPED0001"
        assert device["device_type"]["model"] == "WS-C2960-24TT-L"
        assert device["site"]["name"] == PREFIX + "DC1"

    def test_hand_entered_data_stays_marked_as_such(self, netbox, lab):
        """A typed serial and an observed one must never look alike."""
        rows = netbox.all(REQUESTS, {"address": "192.0.2.110"})
        assert rows and rows[0]["manually_entered"] is True

    def test_a_model_is_required(self, netbox, lab):
        from snmpinv.snmp import SnmpTimeoutError

        class NoSnmp:
            def collect(self, host):
                raise SnmpTimeoutError("nope")

        entry = submit(netbox, "192.0.2.111")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        onboarding.run_jobs(netbox, NoSnmp(), None, jobs)

        with pytest.raises(NetBoxError) as exc:
            netbox.post_raw("%s%s/manual/" % (REQUESTS, entry["id"]),
                            {"name": "x", "manufacturer": "Cisco", "model": ""},
                            label="manual entry")
        assert "400" in str(exc.value)
