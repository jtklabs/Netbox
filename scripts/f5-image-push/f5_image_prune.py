#!/usr/bin/env python3
"""List and delete old software/hotfix installer ISOs on every unit in devices.csv.

Deleting here means removing installer files from /shared/images via
DELETE /mgmt/tm/sys/software/image/<name> (and .../hotfix/<name>) — the REST
equivalent of 'tmsh delete sys software image'. Installed boot volumes and
the running software are never touched; only the ISOs an operator could pick
from the GUI Image List / Hotfix List are removed.

Dry-run by default: shows every image and hotfix on each unit and what
--delete would remove. Protect the image you're about to install with --keep.

Usage:
  ./f5_image_prune.py                                        # list only
  ./f5_image_prune.py --keep BIGIP-17.5.1.8-0.0.19.iso --delete
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

try:
    import requests
    import urllib3
except ImportError:
    sys.exit("This tool needs the 'requests' package: pip install -r requirements.txt")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from f5_image_push import F5Client, Report, free_space_kb, load_devices, load_settings, log

COLLECTIONS = (
    ("image", "/mgmt/tm/sys/software/image"),
    ("hotfix", "/mgmt/tm/sys/software/hotfix"),
)


def prune_device(device, settings, keep, do_delete, report):
    client = F5Client(device, settings["login_provider"], settings["verify_ssl"], settings["timeout"])
    try:
        client.login()
        deleted = candidates = kept = failed = 0
        for kind, path in COLLECTIONS:
            for item in client.get_json(path).get("items", []):
                name = item.get("name", "")
                info = f"{kind} {name} ({item.get('version', '?')}, {item.get('fileSize', '? size')})"
                if name in keep:
                    kept += 1
                    log(device.name, f"keep          {info}")
                elif not do_delete:
                    candidates += 1
                    log(device.name, f"would delete  {info}")
                else:
                    resp = client.session.delete(
                        f"{client.base}{path}/{name}",
                        verify=client.verify, timeout=max(client.timeout, 120),
                    )
                    if resp.ok:
                        deleted += 1
                        log(device.name, f"deleted       {info}")
                        report.record(name, device, "deleted", info)
                    else:
                        failed += 1
                        detail = f"{info} — HTTP {resp.status_code}: {resp.text[:200]}"
                        log(device.name, f"DELETE FAILED {detail}")
                        report.record(name, device, "delete-failed", detail)
        free_kb = free_space_kb(client)
        free = f"{free_kb // 1024} MB free in /shared/images" if free_kb is not None else "free space unknown"
        log(device.name, f"{free}")
        return {"device": device, "status": "failed" if failed else "ok",
                "deleted": deleted, "candidates": candidates, "kept": kept,
                "failed": failed, "free": free}
    except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
        log(device.name, f"FAILED: {exc}")
        return {"device": device, "status": "failed", "error": str(exc)}
    finally:
        client.logout()


def main():
    argp = argparse.ArgumentParser(
        description="List/delete old software and hotfix ISOs on every unit in a CSV.")
    argp.add_argument("--csv", default="devices.csv", help="device inventory CSV (default: devices.csv)")
    argp.add_argument("--config", default="config.ini", help="credentials/settings INI (default: config.ini)")
    argp.add_argument("--workers", type=int, help="parallel devices (default from config)")
    argp.add_argument("--keep", action="append", default=[], metavar="NAME",
                      help="ISO filename to keep (repeatable), e.g. the image you're rolling out")
    argp.add_argument("--delete", action="store_true",
                      help="actually delete; without this the run is a dry-run listing")
    argp.add_argument("--report", default="prune-report.csv",
                      help="append-only history of actual deletions (default: prune-report.csv)")
    args = argp.parse_args()

    settings = load_settings(args.config)
    devices = load_devices(args.csv, settings)
    if not settings["verify_ssl"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    report = Report(args.report)

    mode = "DELETING" if args.delete else "dry-run (nothing will be deleted; add --delete)"
    print(f"pruning installers on {len(devices)} device(s) — {mode}")
    if args.keep:
        print(f"keeping: {', '.join(args.keep)}")
    elif args.delete:
        print("warning: no --keep given — every installer ISO on the units will be deleted")

    keep = set(args.keep)
    with ThreadPoolExecutor(max_workers=args.workers or settings["workers"]) as pool:
        results = list(pool.map(
            lambda dev: prune_device(dev, settings, keep, args.delete, report),
            devices,
        ))

    print("\nsummary:")
    failed = 0
    for res in results:
        dev = res["device"]
        if "error" in res:
            failed += 1
            print(f"  FAILED  {dev.name} ({dev.host}) — {res['error']}")
            continue
        if res["failed"]:
            failed += 1
        action = f"deleted {res['deleted']}" if args.delete else f"would delete {res['candidates']}"
        print(f"  {dev.name} ({dev.host}): {action}, kept {res['kept']}"
              + (f", {res['failed']} delete(s) failed" if res["failed"] else "")
              + f" — {res['free']}")
    if failed:
        print(f"\n{failed} of {len(results)} device(s) had failures")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
