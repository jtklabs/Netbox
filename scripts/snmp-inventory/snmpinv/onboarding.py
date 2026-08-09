"""Work the onboarding queue in the Discovery plugin.

The sweep in snmp_inventory.py answers "what am I responsible for?" and scans
all of it. This answers a different question — "has anyone asked me to onboard
something?" — and it is the half a person interacts with: somebody types an IP
into NetBox and this is what eventually acts on it.

Two kinds of job come back from a check-in:

    scan    walk the device and report what is there. Writes NOTHING. The
            request goes to `review` and waits for a person.
    apply   the person approved it. Now create the device for real.

The scan is deliberately read-only. The pipeline this replaced applied
everything automatically because its review queue was a paid feature, and
onboarding is exactly where a wrong site or a duplicate serial is cheapest to
catch.

Apply re-walks the device rather than replaying the preview. A preview can be
hours old by the time somebody gets to it, and the device is right there to
ask. If the hardware has changed underneath the approval — a different serial
or model — the request goes back to review instead of applying, because the
person approved a specific box and this is no longer that box.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading

from .collect import Collector
from .model import ScanResult, build_scan_result
from .netbox import NetBox, NetBoxError
from .snmp import SnmpAuthError, SnmpError, SnmpTimeoutError
from .sync import Syncer

log = logging.getLogger(__name__)

# NetBox writes during an apply are serialised. The SNMP walks are the slow
# part and parallelise happily, but two applies running at once would race to
# create the same manufacturer or device type and one would lose to a 400.
# Same reasoning, and the same shape, as the sweep in snmp_inventory.py.
_write_lock = threading.Lock()

CHECK_IN_ENDPOINT = "/plugins/discovery/pollers/check-in/"
REQUEST_ENDPOINT = "/plugins/discovery/onboarding-requests/"
PLUGIN_NAME = "netbox_discovery"


class OnboardingUnavailable(Exception):
    """The Discovery plugin is not installed on this NetBox."""


def plugin_available(netbox: NetBox) -> bool:
    """Probe for the plugin. Only used to explain a failure, never before one.

    Deliberately not called on the happy path. This runs from cron every minute
    or two on every poller, and almost every run has nothing to do — so the
    idle cost should be the single check-in request and nothing else. Probing
    first would triple that for no benefit, since a missing plugin shows up
    perfectly well as a 404 on the check-in itself.
    """
    return (
        netbox.plugin_installed(PLUGIN_NAME)
        and netbox.endpoint_available(REQUEST_ENDPOINT)
    )


def check_in(netbox: NetBox, poller_name: str, version: str = "",
             summary: str = "", claim: bool = True, limit: int = 25) -> list[dict]:
    """Announce this poller and take whatever work is waiting for it."""
    payload = {
        "name": poller_name,
        "version": version,
        "summary": summary,
        "claim": claim,
        "limit": limit,
    }
    response = netbox.post_raw(CHECK_IN_ENDPOINT, payload, label="poller check-in")
    if not response:
        return []
    jobs = response.get("jobs", [])
    if jobs:
        log.info("check-in: %d job(s) waiting", len(jobs))
    return jobs


def run_jobs(netbox: NetBox, collector: Collector, syncer: Syncer,
             jobs: list[dict], dry_run: bool = False, workers: int = 8) -> dict:
    """Do each job and report its outcome back. Returns a count per outcome.

    Jobs run concurrently. A check-in can hand back a batch — a bulk CSV import
    of a floor's worth of switches arrives as one — and each job is dominated
    by waiting on a device that may take seconds to answer or the better part
    of a minute to time out. Doing them one at a time would make a batch of
    twenty take as long as the sum of its slowest members.

    The concurrency is over the SNMP work only; NetBox writes during an apply
    are serialised, as they are in the sweep.
    """
    counts: dict[str, int] = {}
    tally_lock = threading.Lock()

    def tally(key):
        with tally_lock:
            counts[key] = counts.get(key, 0) + 1

    def run_one(job):
        action = job.get("action")
        request_id = job.get("id")
        try:
            if action == "scan":
                return _do_scan(netbox, collector, request_id,
                                job.get("address", ""), dry_run)
            if action == "apply":
                return _do_apply(netbox, collector, syncer, job, dry_run)
            log.warning("request %s: unknown job action %r", request_id, action)
            return "skipped"
        except NetBoxError as exc:
            # The scan itself may have succeeded; only the reporting failed.
            # Leave the request as it is so the next check-in retries it.
            log.error("request %s: could not report back: %s", request_id, exc)
            return "report-failed"

    if len(jobs) == 1 or workers <= 1:
        # Not worth a pool, and keeps the single-job case easy to follow in a log.
        tally(run_one(jobs[0]) if jobs else "skipped")
        return counts

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        for outcome in pool.map(run_one, jobs):
            tally(outcome)
    return counts


def _do_scan(netbox: NetBox, collector: Collector, request_id: int,
             address: str, dry_run: bool) -> str:
    """Walk a device and report what is there, writing nothing."""
    log.info("onboarding %s: scanning", address)
    try:
        facts = collector.collect(address)
    except SnmpTimeoutError:
        _report_scan_failure(netbox, request_id, address, dry_run,
                             "No SNMP response. Check the address is right, that the "
                             "device is reachable from this poller, and that SNMP is "
                             "enabled on it.")
        return "unreachable"
    except SnmpAuthError:
        _report_scan_failure(netbox, request_id, address, dry_run,
                             "The device answered but rejected every credential set "
                             "this poller has. Add its SNMPv3 user, or add the "
                             "credential set to the poller.")
        return "auth-failed"
    except SnmpError as exc:
        _report_scan_failure(netbox, request_id, address, dry_run, str(exc))
        return "failed"

    result = build_scan_result(facts)
    if not result.devices:
        _report_scan_failure(netbox, request_id, address, dry_run,
                             "The device answered but reported no chassis, so there "
                             "is nothing to create.")
        return "no-chassis"

    payload = scan_payload(result)
    if dry_run:
        log.info("[dry-run] would report %s as %s", address, _describe(result))
        return "scanned"
    netbox.post_raw(f"{REQUEST_ENDPOINT}{request_id}/scanned/", payload,
                    label=f"scan result for {address}")
    log.info("onboarding %s: reported %s — awaiting review", address, _describe(result))
    return "scanned"


def _report_scan_failure(netbox: NetBox, request_id: int, address: str,
                         dry_run: bool, message: str) -> None:
    log.warning("onboarding %s: %s", address, message)
    if dry_run:
        return
    netbox.post_raw(
        f"{REQUEST_ENDPOINT}{request_id}/scanned/",
        {"ok": False, "error": message},
        label=f"scan failure for {address}",
    )


def _do_apply(netbox: NetBox, collector: Collector, syncer: Syncer,
              job: dict, dry_run: bool) -> str:
    """Create the device an operator approved, after re-reading it."""
    address = job.get("address", "")
    request_id = job.get("id")
    site_id = job.get("site")
    log.info("onboarding %s: approved, applying", address)

    if site_id is None:
        _report_apply_failure(netbox, request_id, dry_run,
                              "The request has no site, so there is nowhere to create "
                              "the device.")
        return "no-site"

    try:
        facts = collector.collect(address)
    except SnmpError as exc:
        _report_apply_failure(netbox, request_id, dry_run,
                              "Could not re-read the device to apply it: %s" % exc)
        return "unreachable"

    result = build_scan_result(facts)
    if not result.devices:
        _report_apply_failure(netbox, request_id, dry_run,
                              "The device no longer reports a chassis.")
        return "no-chassis"

    changed = _hardware_changed(netbox, request_id, result)
    if changed:
        _report_apply_failure(netbox, request_id, dry_run, changed)
        return "changed"

    _apply_overrides(result, job)

    if dry_run:
        log.info("[dry-run] would create %s at site %s%s", _describe(result), site_id,
                 " for tenant %s" % job["tenant_name"] if job.get("tenant_name") else "")
        return "applied"

    # Serialised: creating the shared taxonomy (manufacturer, device type,
    # platform, role) races otherwise, and the syncer's batched state is not
    # thread safe.
    with _write_lock:
        syncer.sync(result, site_id, scanned_address=address,
                    tenant_id=job.get("tenant"))
        syncer.flush_software_reports()
        device = _find_created_device(netbox, result, site_id)
    if device is None:
        _report_apply_failure(netbox, request_id, dry_run,
                              "The sync ran but the device could not be found "
                              "afterwards. Check the poller log.")
        return "apply-failed"

    netbox.post_raw(f"{REQUEST_ENDPOINT}{request_id}/applied/",
                    {"ok": True, "device": device["id"]},
                    label=f"apply result for {address}")
    log.info("onboarding %s: created %s", address, device.get("name"))
    return "applied"


def _report_apply_failure(netbox: NetBox, request_id: int, dry_run: bool,
                          message: str) -> None:
    log.warning("onboarding request %s: %s", request_id, message)
    if dry_run:
        return
    netbox.post_raw(f"{REQUEST_ENDPOINT}{request_id}/applied/",
                    {"ok": False, "error": message},
                    label=f"apply failure for request {request_id}")


def _hardware_changed(netbox: NetBox, request_id: int, result: ScanResult) -> str:
    """Has the device changed since the operator looked at it?

    They approved a specific box. If the serial or model now differs, applying
    would create something nobody agreed to — a swapped unit, or the address
    reassigned to different hardware entirely.
    """
    try:
        request = netbox.get(f"{REQUEST_ENDPOINT}{request_id}/")
    except NetBoxError:
        # Cannot check; better to apply what is actually there than to stall.
        return ""
    previewed = (request.get("discovered") or {}).get("devices") or []
    if not previewed:
        return ""

    def key(entry):
        return ((entry.get("serial") or "").strip(), (entry.get("model") or "").strip())

    before = sorted(key(d) for d in previewed)
    after = sorted((d.serial.strip(), d.model.strip()) for d in result.devices)
    if before == after:
        return ""
    return (
        "The hardware changed since this was reviewed, so it was not applied. "
        "Reviewed: %s. Now: %s. Look again and re-approve if this is expected."
        % (_pairs(before), _pairs(after))
    )


def _pairs(items) -> str:
    return ", ".join("%s/%s" % (model or "?", serial or "?") for serial, model in items)


def _apply_overrides(result: ScanResult, job: dict) -> None:
    """Apply the operator's name override to the device the address belongs to.

    Only the primary: for a stack the members are named from the master, and
    renaming one member out of three would produce an inconsistent chassis.
    """
    override_name = (job.get("override_name") or "").strip()
    if not override_name:
        return
    primary = result.primary
    if primary is None:
        return
    old = primary.name
    primary.name = override_name
    for device in result.devices:
        if device is primary or not device.name.startswith(old + "-"):
            continue
        device.name = override_name + device.name[len(old):]
    if result.virtual_chassis_name == old:
        result.virtual_chassis_name = override_name


def _find_created_device(netbox: NetBox, result: ScanResult, site_id: int) -> dict | None:
    """Locate the device the sync just created, to report its id back."""
    primary = result.primary
    if primary is None:
        return None
    if primary.serial:
        found = netbox.first("/dcim/devices/", {"serial": primary.serial})
        if found:
            return found
    if primary.name:
        return netbox.first("/dcim/devices/", {"name": primary.name, "site_id": site_id})
    return None


def scan_payload(result: ScanResult) -> dict:
    """Render a scan result as the preview the plugin stores for review.

    Interfaces and modules are summarised rather than sent whole: the review
    page shows counts and the member breakdown, and a 48-port switch's full
    interface list would bloat every request row for something nobody reads at
    that stage. The apply step re-reads the device anyway.
    """
    facts = result.facts
    return {
        "ok": True,
        "sys_name": result.sys_name,
        "sys_descr": (facts.sys_descr if facts else "")[:2000],
        "credential": result.credential_name,
        "devices": [
            {
                "name": device.name,
                "model": device.model,
                "serial": device.serial,
                "manufacturer": device.manufacturer,
                "platform": device.platform,
                "software_version": device.software_version,
                "is_master": bool(device.vc_is_master) or device is result.primary,
                "vc_position": device.vc_position,
                "interfaces": [
                    {"name": i.name, "type": i.type_slug} for i in device.interfaces
                ],
                "modules": [
                    {"bay": m.bay_name, "model": m.model, "serial": m.serial}
                    for m in device.modules
                ],
            }
            for device in result.devices
        ],
        "access_points": [
            {"name": ap.name, "model": ap.model, "serial": ap.serial}
            for ap in result.access_points
        ],
    }


def _describe(result: ScanResult) -> str:
    primary = result.primary
    if primary is None:
        return "nothing identifiable"
    bits = [primary.name]
    if primary.model:
        bits.append("%s %s" % (primary.manufacturer, primary.model))
    if primary.serial:
        bits.append("serial %s" % primary.serial)
    if result.is_stack:
        bits.append("stack of %d" % len(result.devices))
    return " | ".join(bits)
