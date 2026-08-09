#!/usr/bin/env python3
"""Scan network devices over SNMPv3 and write what they report into NetBox.

Built to replace orb-agent + Diode for discovery. The difference that matters:
this reads the device's own ENTITY-MIB for model and serial instead of guessing
the model from sysObjectID through a compiled-in lookup table. A device that
calls itself DCS-7050SX-72Q arrives in NetBox as DCS-7050SX-72Q, and correcting
something by hand is not undone on the next pass.

What it collects
    system info, chassis serial and model, modules and their serials,
    interfaces with types and addresses, Cisco stack membership, the software
    version, and — from Aruba controllers — the access points they terminate.

How it decides what to scan
    Nothing is swept. The poller asks NetBox which addresses belong to it,
    using `poller-<name>` tags applied at region, site or device level, with
    device beating site beating region. See selection.py.

Usage
    ./snmp_inventory.py --config snmp-inventory.conf --dry-run
    ./snmp_inventory.py --config snmp-inventory.conf
    ./snmp_inventory.py --config snmp-inventory.conf --host 10.0.10.5
    ./snmp_inventory.py --config snmp-inventory.conf --list-targets

Start with --dry-run. It performs the full scan and prints every object it
would create or change without writing anything.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snmpinv import config as config_module
from snmpinv import onboarding
from snmpinv import __version__
from snmpinv.collect import Collector, DeviceFacts
from snmpinv.model import ScanResult, build_scan_result
from snmpinv.netbox import NetBox, NetBoxError
from snmpinv.selection import Target, resolve_ownership, select_targets
from snmpinv.snmp import SnmpAuthError, SnmpError, SnmpTimeoutError, SnmpToolMissing
from snmpinv.sync import Syncer

log = logging.getLogger("snmp-inventory")

# NetBox writes are serialised. The API is not the bottleneck — SNMP walks are —
# and serialising removes any chance of two workers racing to create the same
# manufacturer or device type and one of them losing to a 400.
_write_lock = threading.Lock()


def main(argv=None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose, args.quiet)

    try:
        config = config_module.load(args.config, args.credentials)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 2

    if args.poller:
        config.poller_name = args.poller
    if args.dry_run:
        log.info("dry run — no changes will be written to NetBox")

    try:
        config.validate()
    except ValueError as exc:
        log.error("configuration is incomplete: %s", exc)
        return 2

    netbox = NetBox(
        config.netbox.url,
        config.netbox.token,
        verify_ssl=config.netbox.verify_ssl,
        timeout=config.netbox.timeout,
        dry_run=args.dry_run,
    )

    collector = Collector(
        config.credentials,
        timeout=config.snmp.timeout,
        retries=config.snmp.retries,
        use_bulk=config.snmp.use_bulk,
    )
    syncer = Syncer(netbox, config.sync)

    if args.onboard:
        return run_onboarding(netbox, config, collector, syncer, args)

    try:
        targets = build_target_list(netbox, config, args)
    except (NetBoxError, ValueError) as exc:
        log.error("could not work out what to scan: %s", exc)
        return 1

    if not targets:
        log.warning("no targets selected — check the poller tags in NetBox")
        return 0

    if args.list_targets:
        print_targets(targets)
        return 0

    log.info("scanning %d targets with %d workers", len(targets), config.snmp.workers)
    started = time.time()
    scanned = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.snmp.workers) as pool:
        futures = {pool.submit(scan_one, collector, target): target for target in targets}
        for future in concurrent.futures.as_completed(futures):
            target = futures[future]
            try:
                result = future.result()
            except SnmpToolMissing as exc:
                log.error("%s", exc)
                return 3
            except SnmpTimeoutError:
                log.warning("%s: no SNMP response", target.address)
                failed += 1
                continue
            except SnmpAuthError as exc:
                log.warning("%s: no credential set accepted (%s)", target.address, exc)
                failed += 1
                continue
            except SnmpError as exc:
                log.warning("%s: SNMP failed (%s)", target.address, exc)
                failed += 1
                continue

            scanned += 1
            describe(result)
            if args.collect_only:
                continue
            try:
                with _write_lock:
                    syncer.sync(result, target.site_id, scanned_address=target.address)
            except NetBoxError as exc:
                log.error("%s: writing to NetBox failed: %s", target.address, exc)
                failed += 1

    if not args.collect_only:
        # One call for the whole fleet rather than one per device.
        syncer.flush_software_reports()

    elapsed = time.time() - started
    log.info(
        "done in %.1fs — %d scanned, %d failed; NetBox: %s",
        elapsed, scanned, failed, netbox.summary(),
    )
    return 0 if scanned else 1


def run_onboarding(netbox: NetBox, config, collector: Collector, syncer: Syncer,
                   args) -> int:
    """Check in with the Discovery plugin and work whatever it hands back.

    Run this on a short timer — a couple of minutes — because a person is
    sitting in NetBox waiting for it. The full sweep is the slow nightly job;
    this is the responsive one.
    """
    started = time.time()
    try:
        # Straight to the check-in. This runs from cron every minute or two on
        # every poller and nearly always has nothing to do, so the idle cost is
        # deliberately one request — probing for the plugin first would triple
        # it to diagnose a problem that almost never exists.
        jobs = onboarding.check_in(
            netbox, config.poller_name,
            version=__version__,
            summary=args.summary,
            claim=not args.dry_run,
            limit=args.limit or 25,
        )
    except NetBoxError as exc:
        if not onboarding.plugin_available(netbox):
            log.error(
                "the Discovery plugin is not installed on %s, so there is no "
                "onboarding queue to work", config.netbox.url
            )
            return 2
        log.error("check-in failed: %s", exc)
        return 1

    if not jobs:
        log.info("check-in: nothing waiting")
        return 0

    counts = onboarding.run_jobs(netbox, collector, syncer, jobs, dry_run=args.dry_run)
    summary = ", ".join("%d %s" % (n, name) for name, n in sorted(counts.items()))
    log.info("onboarding done in %.1fs — %s; NetBox: %s",
             time.time() - started, summary or "nothing done", netbox.summary())
    return 0


def build_target_list(netbox: NetBox, config, args) -> list[Target]:
    """Either the explicit --host list, or whatever NetBox says we own."""
    if args.host:
        ownership = resolve_ownership(netbox, config.poller_name)
        targets = []
        for host in args.host:
            site_id = args.site_id
            device = netbox.first("/dcim/devices/", {"q": host}) if not site_id else None
            if site_id is None and device is not None:
                site_id = (device.get("site") or {}).get("id")
            if site_id is None:
                site_id = _site_from_prefix(netbox, host)
            targets.append(Target(
                address=host,
                site_id=site_id,
                site_name=ownership.site_names.get(site_id, "") if site_id else "",
                source="cli",
            ))
        return targets

    targets = select_targets(
        netbox,
        config.poller_name,
        scan_tag=config.scan_tag,
        include_device_primaries=not args.new_only,
    )
    if args.limit:
        targets = targets[: args.limit]
    return targets


def _site_from_prefix(netbox: NetBox, address: str) -> int | None:
    """Find the site by looking up the most specific prefix containing the IP.

    `?contains=` returns every containing prefix, least specific first, so the
    longest mask has to be picked explicitly rather than taking the first row.
    A /16 scoped to a regional aggregate must not win over the /24 that is
    actually the device's site.
    """
    try:
        prefixes = netbox.all("/ipam/prefixes/", {"contains": address})
    except NetBoxError:
        return None
    best_len = -1
    best_site = None
    for prefix in prefixes:
        try:
            mask = int(prefix["prefix"].split("/")[1])
        except (KeyError, IndexError, ValueError):
            continue
        scope_type = prefix.get("scope_type")
        scope = prefix.get("scope") or {}
        if scope_type == "dcim.site" and mask > best_len:
            best_len, best_site = mask, scope.get("id")
        elif scope_type == "dcim.location" and mask > best_len:
            # A location-scoped prefix still tells us the site, one hop up.
            location = netbox.first("/dcim/locations/", {"id": scope.get("id")})
            if location and location.get("site"):
                best_len, best_site = mask, location["site"]["id"]
    return best_site


def scan_one(collector: Collector, target: Target) -> ScanResult:
    facts: DeviceFacts = collector.collect(target.address)
    return build_scan_result(facts)


def describe(result: ScanResult) -> None:
    """Log a one-line summary of what a device turned out to be."""
    primary = result.primary
    if primary is None:
        log.info("%s: nothing identifiable", result.host)
        return
    bits = [f"{result.host}: {primary.name}"]
    if primary.model:
        bits.append(f"{primary.manufacturer} {primary.model}")
    if primary.serial:
        bits.append(f"serial {primary.serial}")
    if primary.software_version:
        bits.append(f"version {primary.software_version}")
    if result.is_stack:
        bits.append(f"stack of {len(result.devices)}")
    interfaces = sum(len(d.interfaces) for d in result.devices)
    modules = sum(len(d.modules) for d in result.devices)
    if interfaces:
        bits.append(f"{interfaces} interfaces")
    if modules:
        bits.append(f"{modules} modules")
    if result.access_points:
        bits.append(f"{len(result.access_points)} APs")
    log.info(" | ".join(bits))


def print_targets(targets: list[Target]) -> None:
    print(f"{'address':<18} {'site':<24} {'device':<28} source")
    print("-" * 88)
    for target in targets:
        print(f"{target.address:<18} {target.site_name[:24]:<24} "
              f"{(target.device_name or '-')[:28]:<28} {target.source}")
    print(f"\n{len(targets)} targets")


def configure_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # requests logs every connection at INFO, which drowns the scan output.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan devices over SNMPv3 and sync the results into NetBox.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[-1],
    )
    parser.add_argument("--config", default="snmp-inventory.conf",
                        help="poller config file (default: %(default)s)")
    parser.add_argument("--credentials", default="",
                        help="SNMPv3 credentials file (default: the one named in --config)")
    parser.add_argument("--poller", default="",
                        help="override the poller name from the config")
    parser.add_argument("--dry-run", action="store_true",
                        help="scan and report, but write nothing to NetBox")
    parser.add_argument("--collect-only", action="store_true",
                        help="scan and log findings without touching NetBox at all")
    parser.add_argument("--host", action="append", default=[],
                        help="scan this address instead of asking NetBox (repeatable)")
    parser.add_argument("--site-id", type=int, default=None,
                        help="site to file --host results under, when it cannot be derived")
    parser.add_argument("--list-targets", action="store_true",
                        help="print the selected targets and exit")
    parser.add_argument("--onboard", action="store_true",
                        help="work the Discovery plugin's onboarding queue instead "
                             "of sweeping; run this on a short timer")
    parser.add_argument("--summary", default="",
                        help="note shown against this poller in NetBox after check-in")
    parser.add_argument("--new-only", action="store_true",
                        help="only scan IPAM addresses, skipping rescans of known devices")
    parser.add_argument("--limit", type=int, default=0,
                        help="scan at most this many targets")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="warnings and errors only")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
