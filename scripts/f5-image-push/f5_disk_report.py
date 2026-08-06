#!/usr/bin/env python3
"""Report total disk size for every unit in devices.csv via iControl REST.

Reads the same config.ini and devices.csv as f5_image_push.py. For each unit
it queries /mgmt/tm/sys/disk/logical-disk for the logical disks (name, total
size, volume-group usage) and, where util/bash is available, the free space
in /shared/images — the number that matters before pushing an image.

Usage:
  ./f5_disk_report.py                       # table on stdout
  ./f5_disk_report.py --output disks.csv    # also write rows to a CSV
"""

import argparse
import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor

try:
    import requests
    import urllib3
except ImportError:
    sys.exit("This tool needs the 'requests' package: pip install -r requirements.txt")

from f5_image_push import F5Client, free_space_kb, load_devices, load_settings, log


def gb(mb):
    return f"{mb / 1024:.1f} GB"


def disk_report(device, settings):
    client = F5Client(device, settings["login_provider"], settings["verify_ssl"], settings["timeout"])
    try:
        client.login()
        disks = [
            {
                "disk": item.get("name", "?"),
                "total_mb": int(item.get("size", 0)),
                "in_use_mb": int(item.get("vgInUse", 0)),
                "free_mb": int(item.get("vgFree", 0)),
            }
            for item in client.get_json("/mgmt/tm/sys/disk/logical-disk").get("items", [])
        ]
        images_kb = free_space_kb(client)
        log(device.name, f"total {gb(sum(d['total_mb'] for d in disks))} across {len(disks)} disk(s)")
        return {
            "device": device,
            "status": "ok",
            "disks": disks,
            "images_free_mb": images_kb // 1024 if images_kb is not None else None,
        }
    except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
        log(device.name, f"FAILED: {exc}")
        return {"device": device, "status": "failed", "error": str(exc)}
    finally:
        client.logout()


def main():
    argp = argparse.ArgumentParser(description="Report total disk size for every unit in a CSV.")
    argp.add_argument("--csv", default="devices.csv", help="device inventory CSV (default: devices.csv)")
    argp.add_argument("--config", default="config.ini", help="credentials/settings INI (default: config.ini)")
    argp.add_argument("--workers", type=int, help="parallel queries (default from config)")
    argp.add_argument("--output", help="also write one row per disk to this CSV (overwritten each run)")
    args = argp.parse_args()

    settings = load_settings(args.config)
    devices = load_devices(args.csv, settings)
    if not settings["verify_ssl"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    with ThreadPoolExecutor(max_workers=args.workers or settings["workers"]) as pool:
        results = list(pool.map(lambda dev: disk_report(dev, settings), devices))

    name_w = max(len(r["device"].name) for r in results) + 2
    host_w = max(len(r["device"].host) for r in results) + 2
    print(f"\n{'device':<{name_w}}{'host':<{host_w}}{'total':>10}  {'disks':<28}{'/shared/images free':>20}")
    failed = 0
    for res in results:
        dev = res["device"]
        if res["status"] == "failed":
            failed += 1
            print(f"{dev.name:<{name_w}}{dev.host:<{host_w}}{'FAILED':>10}  {res['error']}")
            continue
        total = sum(d["total_mb"] for d in res["disks"])
        detail = ", ".join(f"{d['disk']} {gb(d['total_mb'])}" for d in res["disks"]) or "none reported"
        images = gb(res["images_free_mb"]) if res["images_free_mb"] is not None else "n/a"
        print(f"{dev.name:<{name_w}}{dev.host:<{host_w}}{gb(total):>10}  {detail:<28}{images:>20}")

    if args.output:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(args.output, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["timestamp", "device", "host", "disk", "total_mb",
                             "vg_in_use_mb", "vg_free_mb", "images_free_mb", "status"])
            for res in results:
                dev = res["device"]
                if res["status"] == "failed":
                    writer.writerow([stamp, dev.name, dev.host, "", "", "", "", "", f"failed: {res['error']}"])
                    continue
                for d in res["disks"]:
                    writer.writerow([stamp, dev.name, dev.host, d["disk"], d["total_mb"],
                                     d["in_use_mb"], d["free_mb"], res["images_free_mb"], "ok"])
        print(f"\nwrote {args.output}")

    if failed:
        print(f"\n{failed} of {len(results)} device(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
