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
        if str(entry.get("address", "")).startswith("192.0.2."):
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
        if pfx.get("prefix") == TEST_PREFIX:
            delete("/ipam/prefixes/", pfx["id"])
    for site in sites:
        delete("/dcim/sites/", site["id"])
    for region in netbox.all("/dcim/regions/"):
        if (region.get("slug") or "").startswith(SLUG):
            delete("/dcim/regions/", region["id"])
    for poller in netbox.all("/plugins/discovery/pollers/"):
        if poller.get("name") == POLLER:
            delete("/plugins/discovery/pollers/", poller["id"])
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
def flow(netbox, lab):
    """One request carried through the whole flow by the tests below, in order."""
    return {
        "request": submit(netbox),
        "collector": ReplayCollector(),
        "syncer": Syncer(netbox, SyncOptions(device_role=SLUG + "network")),
    }


class TestOnboardingFlow:

    def test_poller_receives_the_job(self, netbox, flow):
        jobs = onboarding.check_in(netbox, POLLER, version="test")
        mine = [j for j in jobs if j["id"] == flow["request"]["id"]]
        assert mine, "the poller was not offered its own request"
        assert mine[0]["action"] == "scan"
        assert mine[0]["address"] == TEST_ADDRESS
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

    def test_approval_turns_it_into_an_apply_job(self, netbox, flow):
        approve(netbox, flow["request"]["id"])
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

    def test_add_review_approve_end_to_end(self, netbox, lab):
        entry = submit(netbox, "192.0.2.80")
        assert entry["status"] == "pending"

        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        assert jobs and jobs[0]["action"] == "scan"

        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("arista-7050sx"), syncer, jobs)

        # Review: the preview is readable over the API, which is what a caller
        # would decide on.
        after = refresh(netbox, entry["id"])
        assert after["status"] == "review"
        assert after["discovered"]["devices"][0]["model"] == "DCS-7050SX-72Q"

        approved = approve(netbox, entry["id"])
        assert approved["status"] == "approved"
        assert approved["reviewed_by"] is not None

        apply_jobs = [j for j in onboarding.check_in(netbox, POLLER)
                      if j["id"] == entry["id"]]
        assert apply_jobs and apply_jobs[0]["action"] == "apply"
        counts = onboarding.run_jobs(
            netbox, ReplayCollector("arista-7050sx"), syncer, apply_jobs
        )
        assert counts.get("applied") == 1

        final = refresh(netbox, entry["id"])
        assert final["status"] == "applied"
        device = netbox.get("/dcim/devices/%s/" % final["device"]["id"])
        assert device["serial"] == "JPE17240001"

    def test_approve_can_override_name_and_site(self, netbox, lab):
        entry = submit(netbox, "192.0.2.81")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("cisco-2960x"), syncer, jobs)

        approved = approve(netbox, entry["id"], override_name="renamed-by-api")
        assert approved["status"] == "approved"
        assert approved["override_name"] == "renamed-by-api"

        apply_jobs = [j for j in onboarding.check_in(netbox, POLLER)
                      if j["id"] == entry["id"]]
        assert apply_jobs[0]["override_name"] == "renamed-by-api"
        onboarding.run_jobs(netbox, ReplayCollector("cisco-2960x"), syncer, apply_jobs)

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

    def test_reject_over_api_records_the_reason(self, netbox, lab):
        entry = submit(netbox, "192.0.2.83")
        jobs = [j for j in onboarding.check_in(netbox, POLLER) if j["id"] == entry["id"]]
        syncer = Syncer(netbox, SyncOptions(device_role=SLUG + "network"))
        onboarding.run_jobs(netbox, ReplayCollector("cisco-2960x"), syncer, jobs)

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
