#!/usr/bin/env python3
"""Import an existing list of device management IPs into NetBox IPAM.

This is the other half of target selection. The scanner never sweeps a subnet —
it scans addresses NetBox already knows about — so an operator's existing
inventory of device addresses has to get in there first. This does that from a
CSV.

Site membership is not a column. It comes from the prefix the address falls
inside, because prefixes are already scoped to sites in NetBox. That single
indirection is what lets a poller work out both which site a brand-new address
belongs to and, through the site's tags, whether it is the poller's to scan.
The practical consequence: create your prefixes and scope them to sites before
importing, or the addresses land with no site and nothing will scan them.

Addresses are tagged (default `scan`) to mark them as in play for scanning.

Usage
    ./import_ips.py --config snmp-inventory.conf --csv devices.csv --dry-run
    ./import_ips.py --config snmp-inventory.conf --csv devices.csv

CSV format — `address` is the only required column:

    address,dns_name,description
    10.10.1.5,core-sw-01.example.net,Building A core
    10.10.1.6/24,,Building A access
    10.20.0.9,,Dallas edge firewall

A mask is optional. Without one the mask of the containing prefix is used, so
the address is stored the way NetBox expects rather than as a /32.
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from snmpinv import config as config_module
from snmpinv.netbox import NetBox, NetBoxError

log = logging.getLogger("import-ips")

DEFAULT_SCAN_TAG = "scan"


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    try:
        config = config_module.load(args.config, args.credentials)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 2

    if not config.netbox.url or not config.netbox.token:
        log.error("netbox.url and netbox.token must be set (or export NETBOX_TOKEN)")
        return 2

    scan_tag = args.tag or config.scan_tag or DEFAULT_SCAN_TAG
    netbox = NetBox(
        config.netbox.url, config.netbox.token,
        verify_ssl=config.netbox.verify_ssl, timeout=config.netbox.timeout,
        dry_run=args.dry_run,
    )

    try:
        rows = read_csv(args.csv)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 2
    if not rows:
        log.error("%s contains no address rows", args.csv)
        return 2
    log.info("read %d addresses from %s", len(rows), args.csv)

    if scan_tag:
        netbox.ensure_tag(scan_tag, name=scan_tag.replace("-", " ").title())

    # Prefixes are fetched once and matched locally. One /ipam/prefixes/?contains=
    # call per address would be a request per row, and an import is thousands of
    # rows; the whole prefix table is a few hundred.
    prefixes = load_prefixes(netbox)
    log.info("loaded %d prefixes to resolve masks and sites", len(prefixes))

    imported = skipped = unscoped = failed = 0
    for row in rows:
        try:
            outcome = import_one(netbox, row, prefixes, scan_tag)
        except NetBoxError as exc:
            log.error("%s: %s", row["address"], exc)
            failed += 1
            continue
        if outcome == "created":
            imported += 1
        elif outcome == "exists":
            skipped += 1
        elif outcome == "no-prefix":
            unscoped += 1

    log.info(
        "%d created, %d already present, %d with no containing prefix, %d failed",
        imported, skipped, unscoped, failed,
    )
    if unscoped:
        log.warning(
            "%d addresses had no containing prefix — they were imported as /32 with no "
            "site, and no poller will select them until a prefix covering them exists "
            "and is scoped to a site",
            unscoped,
        )
    if netbox.dry_run:
        log.info("dry run — nothing was written")
    return 0 if not failed else 1


def read_csv(path: str) -> list[dict]:
    """Read the CSV, keeping only rows with a usable address."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no header row")
        fields = {name.strip().lower() for name in reader.fieldnames}
        if "address" not in fields:
            raise ValueError(f"{path} needs an 'address' column (found: {sorted(fields)})")
        for line_number, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            address = row.get("address", "")
            if not address or address.startswith("#"):
                continue
            if not _looks_like_address(address):
                log.warning("line %d: %r is not an IP address — skipped", line_number, address)
                continue
            rows.append(row)
    return rows


def _looks_like_address(value: str) -> bool:
    try:
        ipaddress.ip_interface(value)
        return True
    except ValueError:
        return False


def load_prefixes(netbox: NetBox) -> list[tuple]:
    """All prefixes as (network, site_id), longest mask first.

    Sorted so the first match is the most specific one — a /24 scoped to a site
    must win over a /16 aggregate scoped to a region.
    """
    entries = []
    for prefix in netbox.all("/ipam/prefixes/"):
        try:
            network = ipaddress.ip_network(prefix["prefix"], strict=False)
        except (KeyError, ValueError):
            continue
        site_id = None
        if prefix.get("scope_type") == "dcim.site":
            site_id = (prefix.get("scope") or {}).get("id")
        entries.append((network, site_id, prefix.get("scope_type")))
    entries.sort(key=lambda item: item[0].prefixlen, reverse=True)
    return entries


def match_prefix(address: ipaddress._BaseAddress, prefixes: list[tuple]):
    for network, site_id, scope_type in prefixes:
        if address.version == network.version and address in network:
            return network, site_id, scope_type
    return None, None, None


def import_one(netbox: NetBox, row: dict, prefixes: list[tuple], scan_tag: str) -> str:
    raw = row["address"]
    interface = ipaddress.ip_interface(raw)
    address = interface.ip
    has_explicit_mask = "/" in raw

    network, site_id, scope_type = match_prefix(address, prefixes)
    if has_explicit_mask:
        cidr = str(interface)
    elif network is not None:
        cidr = f"{address}/{network.prefixlen}"
    else:
        cidr = f"{address}/{32 if address.version == 4 else 128}"

    existing = netbox.first("/ipam/ip-addresses/", {"address": cidr})
    if existing is not None:
        # Already imported. Add the scan tag if a previous import predates it,
        # but never remove tags somebody else put on.
        if scan_tag:
            tags = [t.get("slug") for t in existing.get("tags", [])]
            if scan_tag not in tags:
                netbox.update(
                    "/ipam/ip-addresses/", existing["id"],
                    {"tags": [{"slug": slug} for slug in tags + [scan_tag]]},
                    label=f"ip {cidr} tags",
                )
        return "exists"

    payload = {"address": cidr, "status": "active"}
    if row.get("dns_name"):
        payload["dns_name"] = row["dns_name"]
    if row.get("description"):
        payload["description"] = row["description"]
    if scan_tag:
        payload["tags"] = [{"slug": scan_tag}]
    netbox.create("/ipam/ip-addresses/", payload, label=f"ip {cidr}")

    if network is None:
        return "no-prefix"
    if site_id is None and scope_type != "dcim.site":
        log.debug("%s sits in %s which is scoped to %s, not a site", cidr, network, scope_type)
    return "created"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import device management IPs into NetBox IPAM for SNMP scanning.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[-1],
    )
    parser.add_argument("--config", default="snmp-inventory.conf",
                        help="poller config file (default: %(default)s)")
    parser.add_argument("--credentials", default="", help="SNMPv3 credentials file")
    parser.add_argument("--csv", required=True, help="CSV of addresses to import")
    parser.add_argument("--tag", default="",
                        help=f"tag to apply (default: poller.scan_tag, else {DEFAULT_SCAN_TAG})")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
