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

Discovery rules (rules.py) run on both: the scan reports what the rules filled
in, so the review sees exactly what will be created, and the apply re-reads
the device and fills it in again the same way.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Sequence

from .collect import Collector
from .model import (
    ContextRecord,
    DeviceRecord,
    InterfaceRecord,
    ModuleRecord,
    ScanResult,
    build_scan_result,
)
from .netbox import NetBox, NetBoxError
from .rules import Rule, apply_rules, summarise
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
             jobs: list[dict], dry_run: bool = False, workers: int = 8,
             rules: Sequence[Rule] = (), strip_domains: Sequence[str] = ()) -> dict:
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
                return _do_scan(netbox, collector, syncer, request_id,
                                job.get("address", ""), dry_run, rules, strip_domains)
            if action == "apply":
                return _do_apply(netbox, collector, syncer, job, dry_run, rules,
                                 strip_domains)
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


def _do_scan(netbox: NetBox, collector: Collector, syncer: Syncer, request_id: int,
             address: str, dry_run: bool, rules: Sequence[Rule] = (),
             strip_domains: Sequence[str] = ()) -> str:
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

    result = build_scan_result(facts, strip_domains=strip_domains)
    if not result.devices:
        _report_scan_failure(netbox, request_id, address, dry_run,
                             "The device answered but reported no chassis, so there "
                             "is nothing to create.")
        return "no-chassis"
    _fill_from_rules(result, rules, address)

    payload = scan_payload(result)
    if dry_run:
        log.info("[dry-run] would report %s as %s", address, _describe(result))
        return "scanned"

    reported = netbox.post_raw(f"{REQUEST_ENDPOINT}{request_id}/scanned/", payload,
                               label=f"scan result for {address}")

    # The server decides whether this one needs a person. A clean scan comes
    # back already approved, and applying it here — with the reading still in
    # hand — saves a whole check-in cycle and a second walk of the device.
    status = (reported or {}).get("status")
    if status == "approved":
        log.info("onboarding %s: %s — nothing to query, applying now",
                 address, _describe(result))
        return _apply_result(netbox, syncer, reported, result, address, dry_run)

    log.info("onboarding %s: reported %s — held for review (%s)",
             address, _describe(result), (reported or {}).get("error") or "policy")
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
              job: dict, dry_run: bool, rules: Sequence[Rule] = (),
              strip_domains: Sequence[str] = ()) -> str:
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

    from_preview = False
    try:
        result = build_scan_result(collector.collect(address), strip_domains=strip_domains)
    except SnmpError as exc:
        # The device is not answering now, but somebody already reviewed a
        # reading of it. Applying what they approved beats making them start
        # again because a switch happened to be rebooting.
        result = _reviewed_result(netbox, request_id, address)
        if result is None:
            _report_apply_failure(
                netbox, request_id, dry_run,
                "Could not re-read the device to apply it (%s), and there is no "
                "stored scan to fall back on." % exc,
            )
            return "unreachable"
        from_preview = True
        log.warning(
            "onboarding %s: unreachable now (%s) — applying the reading that was "
            "reviewed instead", address, exc,
        )

    if not result.devices:
        _report_apply_failure(netbox, request_id, dry_run,
                              "The device no longer reports a chassis.")
        return "no-chassis"

    if not from_preview:
        changed = _hardware_changed(netbox, request_id, result)
        if changed:
            _report_apply_failure(netbox, request_id, dry_run, changed)
            return "changed"
        # Only now, after the box has been judged on what it reports itself:
        # the same rules the scan was reported under, so what was reviewed is
        # what gets created. The stored preview taken above already carries
        # their work and is not re-judged -- the person approved that reading.
        _fill_from_rules(result, rules, address)

    _apply_overrides(result, job)

    if dry_run:
        log.info("[dry-run] would create %s at site %s%s", _describe(result), site_id,
                 " for tenant %s" % job["tenant_name"] if job.get("tenant_name") else "")
        return "applied"

    return _write_and_report(netbox, syncer, request_id, address, result, site_id,
                             job.get("tenant"), job.get("vrf"))


def _apply_result(netbox: NetBox, syncer: Syncer, request: dict,
                  result: ScanResult, address: str, dry_run: bool) -> str:
    """Create the device straight from a scan the server has just approved.

    The reading is already in hand, so this needs neither a second walk of the
    device nor another check-in — the clean path is scan, apply, done.
    """
    site = request.get("site") or {}
    override = request.get("override_site") or {}
    site_id = override.get("id") or site.get("id")
    if site_id is None:
        _report_apply_failure(netbox, request["id"], dry_run,
                              "The request has no site, so there is nowhere to "
                              "create the device.")
        return "no-site"
    if dry_run:
        return "applied"
    _apply_overrides(result, {
        "override_name": request.get("override_name") or "",
        "override_model": request.get("override_model") or "",
    })
    tenant = (request.get("tenant") or {}).get("id")
    # The routing table the request was placed in; None is the global table.
    vrf = (request.get("vrf") or {}).get("id")
    return _write_and_report(netbox, syncer, request["id"], address, result,
                             site_id, tenant, vrf)


def _write_and_report(netbox: NetBox, syncer: Syncer, request_id: int, address: str,
                      result: ScanResult, site_id: int, tenant_id, vrf_id=None) -> str:
    """The write half of an apply, shared by both routes into it.

    `vrf_id` is the routing table the request was placed in, so the device's
    addresses go into that table and not the one next to it.
    """
    # Serialised: creating the shared taxonomy (manufacturer, device type,
    # platform, role) races otherwise, and the syncer's batched state is not
    # thread safe.
    with _write_lock:
        syncer.sync(result, site_id, scanned_address=address, tenant_id=tenant_id,
                    vrf_id=vrf_id)
        syncer.flush_software_reports()
        device = _find_created_device(netbox, result, site_id)

    if device is None:
        _report_apply_failure(netbox, request_id, False,
                              "The sync ran but the device could not be found "
                              "afterwards. Check the poller log.")
        return "apply-failed"

    netbox.post_raw(f"{REQUEST_ENDPOINT}{request_id}/applied/",
                    {"ok": True, "device": device["id"]},
                    label=f"apply result for {address}")
    log.info("onboarding %s: created %s", address, device.get("name"))
    return "applied"


def _reviewed_result(netbox: NetBox, request_id: int, address: str) -> ScanResult | None:
    """The preview the operator approved, rebuilt into something appliable."""
    try:
        request = netbox.get(f"{REQUEST_ENDPOINT}{request_id}/")
    except NetBoxError:
        return None
    payload = request.get("discovered") or {}
    if not payload.get("devices"):
        return None
    return scan_result_from_payload(payload, host=address)


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
    discovered = request.get("discovered") or {}
    previewed = discovered.get("devices") or []
    if not previewed:
        return ""

    # Compare what the device itself said both times. A model or serial a
    # rule supplied at scan time is in the preview with the device's own
    # answer (usually nothing) kept under rules_applied; that answer is what
    # the re-read is judged against here, before the rules run again. Without
    # this every rule-filled value would read as a hardware change, and so
    # would a rule added between review and apply -- neither is a different
    # box.
    device_said = {
        (entry.get("device"), entry.get("field")): (entry.get("previous") or "")
        for entry in discovered.get("rules_applied") or []
    }

    def reported(entry, field):
        return device_said.get((entry.get("name"), field), entry.get(field) or "").strip()

    def key(entry):
        return (reported(entry, "serial"), reported(entry, "model"))

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


def _fill_from_rules(result: ScanResult, rules: Sequence[Rule], address: str) -> None:
    """Let the discovery rules fill in what the device left blank, and say so."""
    applied = apply_rules(result, rules)
    if applied:
        log.info("onboarding %s: rules filled in %s", address, summarise(applied))


def _apply_overrides(result: ScanResult, job: dict) -> None:
    """Apply the operator's name override to the device the address belongs to.

    Only the primary: for a stack the members are named from the master, and
    renaming one member out of three would produce an inconsistent chassis.
    """
    primary = result.primary

    # The model first, and only when the device reported none of its own: it
    # is what a reviewer supplies for a platform that publishes no model —
    # a Firepower 2120 among them — and without it there is no device type and
    # nothing gets created. What the device says still wins, so an override
    # left on a request cannot quietly override a later scan that reads one.
    override_model = (job.get("override_model") or "").strip()
    if override_model and primary is not None and not primary.model:
        primary.model = override_model

    override_name = (job.get("override_name") or "").strip()
    if not override_name:
        return
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
    if primary.context is not None and primary.context.is_vdc:
        # The VDC lives on the chassis; that is the device the request produced.
        if primary.context.chassis_serial:
            return netbox.first("/dcim/devices/", {"serial": primary.context.chassis_serial})
        return None
    # Name at the site first: it is unique there, where a serial may now sit
    # on more than one record and would point a duplicate's request at the
    # original.
    if primary.name:
        found = netbox.first("/dcim/devices/", {"name": primary.name, "site_id": site_id})
        if found:
            return found
    if primary.serial:
        return netbox.first("/dcim/devices/", {"serial": primary.serial})
    return None


def scan_payload(result: ScanResult) -> dict:
    """Render a scan result as the preview the plugin stores for review.

    Stored in full, not summarised. The review page only shows counts and the
    member breakdown, so most of this is never displayed — but it is what makes
    the stored preview *sufficient to apply from*. Without the addresses, MACs
    and speeds, a request whose device went offline between review and approval
    could not be completed at all, and somebody would have to start again for
    no reason. A 48-port stack costs about 5 KB.
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
                # A Nexus VDC or a vCMP guest, and the chassis it belongs to;
                # None for a box of its own. The review page says which.
                "context": device.context.as_dict() if device.context else None,
                "interfaces": [
                    {
                        "name": i.name,
                        "type": i.type_slug,
                        "enabled": i.enabled,
                        "mtu": i.mtu,
                        "mac_address": i.mac_address,
                        "description": i.description,
                        "speed_kbps": i.speed_kbps,
                        "ip_addresses": list(i.ip_addresses),
                    }
                    for i in device.interfaces
                ],
                "modules": [
                    {"bay": m.bay_name, "model": m.model, "serial": m.serial}
                    for m in device.modules
                ],
            }
            for device in result.devices
        ],
        "access_points": [
            {"name": ap.name, "model": ap.model, "serial": ap.serial,
             "manufacturer": ap.manufacturer, "platform": ap.platform,
             "software_version": ap.software_version, "description": ap.description}
            for ap in result.access_points
        ],
        # Which of the values above a rule supplied rather than the device,
        # so the review page can show the two apart.
        "rules_applied": [entry.as_dict() for entry in result.rules_applied],
    }


def scan_result_from_payload(payload: dict, host: str = "") -> ScanResult:
    """Rebuild a ScanResult from a stored preview.

    Used when the device cannot be reached at apply time. The operator approved
    this exact reading; applying it is better than making them start over
    because a switch happened to be rebooting.
    """
    devices = []
    for entry in payload.get("devices", []):
        devices.append(DeviceRecord(
            name=entry.get("name", ""),
            serial=entry.get("serial", ""),
            model=entry.get("model", ""),
            manufacturer=entry.get("manufacturer", ""),
            platform=entry.get("platform", ""),
            software_version=entry.get("software_version", ""),
            vc_position=entry.get("vc_position"),
            vc_is_master=bool(entry.get("is_master")),
            context=ContextRecord.from_dict(entry.get("context")),
            interfaces=[
                InterfaceRecord(
                    name=i.get("name", ""),
                    type_slug=i.get("type", "other"),
                    enabled=i.get("enabled", True),
                    mtu=i.get("mtu"),
                    mac_address=i.get("mac_address", ""),
                    description=i.get("description", ""),
                    speed_kbps=i.get("speed_kbps"),
                    ip_addresses=list(i.get("ip_addresses") or []),
                )
                for i in entry.get("interfaces", [])
            ],
            modules=[
                ModuleRecord(
                    bay_name=m.get("bay", ""),
                    model=m.get("model", ""),
                    serial=m.get("serial", ""),
                    manufacturer=m.get("manufacturer", entry.get("manufacturer", "")),
                )
                for m in entry.get("modules", [])
            ],
        ))
    result = ScanResult(host=host, sys_name=payload.get("sys_name", ""), devices=devices)
    result.access_points = [
        DeviceRecord(
            name=entry.get("name", ""),
            model=entry.get("model", ""),
            serial=entry.get("serial", ""),
            manufacturer=entry.get("manufacturer", "Aruba Networks"),
            platform=entry.get("platform", "ArubaOS"),
            software_version=entry.get("software_version", ""),
            description=entry.get("description", ""),
            is_access_point=True,
        )
        for entry in payload.get("access_points", [])
    ]
    if len([d for d in devices if d.vc_position is not None]) > 1:
        primary = result.primary
        result.virtual_chassis_name = primary.name if primary else ""
    return result


def _describe(result: ScanResult) -> str:
    primary = result.primary
    if primary is None:
        return "nothing identifiable"
    bits = [primary.name]
    if primary.model:
        bits.append("%s %s" % (primary.manufacturer, primary.model))
    if primary.serial:
        bits.append("serial %s" % primary.serial)
    if primary.context is not None:
        bits.append(primary.context.detail)
    if result.is_stack:
        bits.append("stack of %d" % len(result.devices))
    return " | ".join(bits)
