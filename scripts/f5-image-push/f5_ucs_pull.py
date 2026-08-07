#!/usr/bin/env python3
"""Pull a UCS archive (full config backup) from every unit in devices.csv.

For each unit: save a fresh UCS on the box (POST /mgmt/tm/sys/ucs), download
it in 1 MB chunks to this server via /mgmt/shared/file-transfer/ucs-downloads/,
md5-verify the local copy against md5sum on the unit, and append the outcome
to a persistent report (same append-only behavior as push-report.csv).

UCS archives contain the unit's ENTIRE configuration including SSL private
keys and the user database — treat the output directory as secret material.
Set ucs_passphrase in config.ini to have the units encrypt the archives.

Usage:
  ./f5_ucs_pull.py                     # saves into ucs-backups/
  ./f5_ucs_pull.py --cleanup           # also delete the UCS from each unit
"""

import argparse
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

try:
    import requests
    import urllib3
except ImportError:
    sys.exit("This tool needs the 'requests' package: pip install -r requirements.txt")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from f5_image_push import (
    MAX_CHUNK, F5Client, Report, load_devices, load_settings, log, md5_of, remote_md5,
)

UCS_DIR = "/var/local/ucs"
# Saving a UCS on a large config can take several minutes.
SAVE_TIMEOUT = 600


class UcsReport(Report):
    COLUMNS = ["timestamp", "ucs", "device", "host", "status", "detail"]


def save_ucs(client, name, passphrase):
    payload = {"command": "save", "name": name}
    if passphrase:
        payload["options"] = [{"passphrase": passphrase}]
    client.post_json("/mgmt/tm/sys/ucs", payload, timeout=SAVE_TIMEOUT)


def ucs_size(client, name):
    """Size in bytes via stat on the unit, or None if util/bash unavailable."""
    try:
        result = client.post_json("/mgmt/tm/util/bash", {
            "command": "run",
            "utilCmdArgs": f"-c \"stat -c %s '{UCS_DIR}/{name}'\"",
        })
        return int(result.get("commandResult", "").split()[0])
    except (requests.RequestException, ValueError, IndexError):
        return None


def download_ucs(client, name, dest, total):
    """Chunked ranged download of the UCS to dest; returns bytes written."""
    uri = f"{client.base}/mgmt/shared/file-transfer/ucs-downloads/{name}"

    def get_range(start, end, known_total):
        resp = client.session.get(
            uri,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": f"{start}-{end}/{known_total}",
            },
            verify=client.verify,
            timeout=max(client.timeout, 120),
        )
        resp.raise_for_status()
        return resp

    if total is None:
        # stat wasn't available: probe one byte; the response's Content-Range
        # header carries the real total ("0-0/52428800").
        probe = get_range(0, 0, 0)
        total = int(probe.headers["Content-Range"].split("/")[-1])

    written = 0
    with open(dest, "wb") as fh:
        while written < total:
            end = min(written + MAX_CHUNK, total) - 1
            resp = get_range(written, end, total)
            fh.write(resp.content)
            written += len(resp.content)
    return written


def delete_remote_ucs(client, name):
    """Best-effort removal of the UCS from the unit after download."""
    try:
        resp = client.session.delete(
            f"{client.base}/mgmt/tm/sys/ucs/{name}",
            verify=client.verify, timeout=client.timeout,
        )
        if resp.ok:
            return True
    except requests.RequestException:
        pass
    try:
        client.post_json("/mgmt/tm/util/bash", {
            "command": "run",
            "utilCmdArgs": f"-c \"rm -f '{UCS_DIR}/{name}'\"",
        })
        return True
    except requests.RequestException:
        return False


def pull_from_device(device, settings, outdir, cleanup, stamp, report):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", device.name)
    name = f"{safe}-{stamp}.ucs"
    dest = os.path.join(outdir, name)
    client = F5Client(device, settings["login_provider"], settings["verify_ssl"], settings["timeout"])
    try:
        client.login()
        log(device.name, f"saving UCS on the unit as {name} (can take minutes on large configs)")
        save_ucs(client, name, settings["ucs_passphrase"])
        total = ucs_size(client, name)
        log(device.name, "downloading" + (f" ({total // (1024 * 1024)} MB)" if total else ""))
        written = download_ucs(client, name, dest, total)
        device_md5 = remote_md5(client, name, UCS_DIR)
        if device_md5 is None:
            verified = "md5 check unavailable (util/bash disabled)"
        elif device_md5 != md5_of(dest):
            raise RuntimeError(f"md5 mismatch: local copy differs from {UCS_DIR}/{name} on the unit")
        else:
            verified = "md5 verified"
        if cleanup:
            removed = delete_remote_ucs(client, name)
            log(device.name, "removed UCS from the unit" if removed
                else "could not remove UCS from the unit — delete it manually")
        log(device.name, f"done — {written // (1024 * 1024)} MB saved to {dest} ({verified})")
        report.record(name, device, "downloaded", f"{written} bytes to {dest}, {verified}")
        return {"device": device, "status": "downloaded", "dest": dest}
    except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
        log(device.name, f"FAILED: {exc}")
        report.record(name, device, "failed", str(exc))
        return {"device": device, "status": "failed", "error": str(exc)}
    finally:
        client.logout()


def main():
    argp = argparse.ArgumentParser(description="Pull a UCS backup from every unit in a CSV.")
    argp.add_argument("--csv", default="devices.csv", help="device inventory CSV (default: devices.csv)")
    argp.add_argument("--config", default="config.ini", help="credentials/settings INI (default: config.ini)")
    argp.add_argument("--workers", type=int, help="parallel pulls (default from config)")
    argp.add_argument("--outdir", default="ucs-backups", help="local directory for archives (default: ucs-backups)")
    argp.add_argument("--report", default="ucs-report.csv",
                      help="append-only run history CSV (default: ucs-report.csv)")
    argp.add_argument("--cleanup", action="store_true",
                      help="delete the UCS from each unit after a verified download")
    args = argp.parse_args()

    settings = load_settings(args.config)
    devices = load_devices(args.csv, settings)
    if not settings["verify_ssl"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    os.makedirs(args.outdir, exist_ok=True)
    report = UcsReport(args.report)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    print(f"pulling UCS from {len(devices)} device(s) into {args.outdir}/ "
          f"(report: {args.report}{', cleanup on' if args.cleanup else ''})")
    if not settings["ucs_passphrase"]:
        print("note: archives are UNENCRYPTED (no ucs_passphrase in config.ini) "
              "and contain private keys — guard the output directory")

    with ThreadPoolExecutor(max_workers=args.workers or settings["workers"]) as pool:
        results = list(pool.map(
            lambda dev: pull_from_device(dev, settings, args.outdir, args.cleanup, stamp, report),
            devices,
        ))

    print("\nsummary:")
    failed = 0
    for res in results:
        dev = res["device"]
        if res["status"] == "failed":
            failed += 1
            print(f"  FAILED      {dev.name} ({dev.host}) — {res['error']}")
        else:
            print(f"  downloaded  {dev.name} ({dev.host}) -> {res['dest']}")
    if failed:
        print(f"\n{failed} of {len(results)} device(s) failed")
        return 1
    print(f"\nall {len(results)} backup(s) pulled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
