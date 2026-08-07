#!/usr/bin/env python3
"""Push an F5 BIG-IP software image (.iso) to a fleet of units over iControl REST.

The upload endpoint (/mgmt/cm/autodeploy/software-image-uploads/) writes the
file into /shared/images on the unit — the directory the BIG-IP GUI reads for
System >> Software Management : Image List (Hotfix List for hotfix ISOs). Once
the upload finishes and the unit verifies the checksum, the image is visible
and installable in the UI with no further action on the box.

Endpoints used (all iControl REST over HTTPS on the management interface):
  POST   /mgmt/shared/authn/login                          get an auth token
  PATCH  /mgmt/shared/authz/tokens/{token}                 extend token lifetime
  POST   /mgmt/cm/autodeploy/software-image-uploads/{iso}  chunked upload -> /shared/images
  GET    /mgmt/tm/sys/software/image                       confirm the unit lists it
  GET    /mgmt/tm/sys/software/hotfix                      (hotfix ISOs list here)
  DELETE /mgmt/shared/authz/tokens/{token}                 log out

Usage:
  ./f5_image_push.py --image BIGIP-17.5.1.8-0.0.19.iso --csv devices.csv
  ./f5_image_push.py --image BIGIP-17.5.1.8-0.0.19.iso --host 10.0.10.11
"""

import argparse
import configparser
import csv
import hashlib
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

try:
    import requests
    import urllib3
except ImportError:
    sys.exit("This tool needs the 'requests' package: pip install -r requirements.txt")

# iControl REST rejects upload chunks larger than 1 MB.
MAX_CHUNK = 1024 * 1024
# BIG-IP caps auth-token lifetime at 36000 s (10 h); ask for the max so slow
# WAN uploads of a ~2 GB ISO never outlive the token.
TOKEN_TIMEOUT = 36000

_print_lock = threading.Lock()


def log(device_name, message):
    with _print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] [{device_name}] {message}", flush=True)


@dataclass
class Device:
    host: str
    name: str
    port: int
    username: str
    password: str


class Report:
    """Append-only run history CSV, written per device as results come in.

    The report is a log, not a source of truth: re-runs never consult it to
    decide what to skip — that decision is made by checking the unit itself
    (find_listed_image). Existing rows are never rewritten or overwritten.
    """

    COLUMNS = ["timestamp", "image", "device", "host", "status", "detail"]

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def record(self, image, device, status, detail=""):
        with self.lock:
            is_new = not os.path.isfile(self.path) or os.path.getsize(self.path) == 0
            with open(self.path, "a", newline="") as fh:
                writer = csv.writer(fh)
                if is_new:
                    writer.writerow(self.COLUMNS)
                writer.writerow([
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    image,
                    device.name,
                    device.host,
                    status,
                    detail,
                ])


class F5Client:
    """Minimal token-authenticated iControl REST client for one unit."""

    def __init__(self, device, login_provider, verify_ssl, timeout):
        self.device = device
        self.login_provider = login_provider
        self.timeout = timeout
        self.base = f"https://{device.host}:{device.port}"
        self.session = requests.Session()
        # Passed per-request rather than via session.verify: a REQUESTS_CA_BUNDLE
        # env var would silently override the session-level setting.
        self.verify = verify_ssl
        self.token = None

    def login(self):
        resp = self.session.post(
            f"{self.base}/mgmt/shared/authn/login",
            json={
                "username": self.device.username,
                "password": self.device.password,
                "loginProviderName": self.login_provider,
            },
            verify=self.verify,
            timeout=self.timeout,
        )
        if resp.status_code == 401:
            raise RuntimeError("authentication failed (check credentials / login_provider)")
        resp.raise_for_status()
        self.token = resp.json()["token"]["token"]
        self.session.headers["X-F5-Auth-Token"] = self.token
        # Extend the token from the 20-minute default; best-effort.
        try:
            self.session.patch(
                f"{self.base}/mgmt/shared/authz/tokens/{self.token}",
                json={"timeout": TOKEN_TIMEOUT},
                verify=self.verify,
                timeout=self.timeout,
            )
        except requests.RequestException:
            pass

    def logout(self):
        if not self.token:
            return
        try:
            self.session.delete(
                f"{self.base}/mgmt/shared/authz/tokens/{self.token}",
                verify=self.verify,
                timeout=self.timeout,
            )
        except requests.RequestException:
            pass
        self.token = None

    def get_json(self, path):
        resp = self.session.get(
            f"{self.base}{path}", verify=self.verify, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def post_json(self, path, payload, timeout=None):
        resp = self.session.post(
            f"{self.base}{path}", json=payload, verify=self.verify,
            timeout=timeout or self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def upload_chunk(self, filename, chunk, start, total):
        end = start + len(chunk) - 1
        resp = self.session.post(
            f"{self.base}/mgmt/cm/autodeploy/software-image-uploads/{filename}",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": f"{start}-{end}/{total}",
            },
            data=chunk,
            verify=self.verify,
            timeout=max(self.timeout, 120),
        )
        resp.raise_for_status()


# Require this much room beyond the image itself so the upload can't run the
# partition to zero (the unit needs working space to checksum/unpack).
SPACE_HEADROOM_KB = 100 * 1024


def free_space_kb(client):
    """Available KB on the filesystem holding /shared/images, or None if the
    unit won't run the check (util/bash disabled on some hardened boxes)."""
    try:
        result = client.post_json("/mgmt/tm/util/bash", {
            "command": "run",
            "utilCmdArgs": '-c "df -Pk /shared/images | tail -1"',
        })
        return int(result.get("commandResult", "").split()[3])
    except (requests.RequestException, ValueError, IndexError):
        return None


SOFTWARE_COLLECTIONS = (
    ("image", "/mgmt/tm/sys/software/image"),
    ("hotfix", "/mgmt/tm/sys/software/hotfix"),
)


def find_listed_image(client, filename):
    """Return the image/hotfix entry for filename if the unit lists it."""
    for _, path in SOFTWARE_COLLECTIONS:
        for item in client.get_json(path).get("items", []):
            if item.get("name") == filename:
                return item
    return None


def prune_other_images(client, keep, report):
    """Delete every software/hotfix ISO on the unit except `keep`.

    Removes installer files from /shared/images via the sys/software
    endpoints (tmsh 'delete sys software image/hotfix' equivalents) —
    installed boot volumes and the running software are never touched.
    A failed delete is logged and reported but doesn't stop the push;
    the space pre-check decides whether the upload can proceed.
    """
    device = client.device
    for kind, path in SOFTWARE_COLLECTIONS:
        for item in client.get_json(path).get("items", []):
            name = item.get("name", "")
            if name == keep:
                continue
            info = f"{kind} {name} ({item.get('version', '?')}, {item.get('fileSize', '? size')})"
            resp = client.session.delete(
                f"{client.base}{path}/{name}",
                verify=client.verify, timeout=max(client.timeout, 120),
            )
            if resp.ok:
                log(device.name, f"pruned {info}")
                report.record(name, device, "pruned", info)
            else:
                log(device.name, f"prune failed: {info} — HTTP {resp.status_code}")
                report.record(name, device, "prune-failed",
                              f"{info} — HTTP {resp.status_code}: {resp.text[:200]}")


def remote_md5(client, filename, directory="/shared/images"):
    """md5sum of a file on the unit, or None if the check can't run (util/bash
    disabled). This is the only trustworthy checksum comparison: the 'checksum'
    metadata field on sys/software/image is NOT the plain md5 of the ISO file,
    so it must never be compared against a locally computed md5."""
    try:
        result = client.post_json("/mgmt/tm/util/bash", {
            "command": "run",
            "utilCmdArgs": f"-c \"md5sum '{directory}/{filename}'\"",
        })
        out = result.get("commandResult", "").split()
        if out and len(out[0]) == 32:
            return out[0]
    except requests.RequestException:
        pass
    return None


def upload_image(client, image_path, chunk_size):
    filename = os.path.basename(image_path)
    total = os.path.getsize(image_path)
    sent = 0
    next_report = 10
    with open(image_path, "rb") as fh:
        while sent < total:
            chunk = fh.read(chunk_size)
            for attempt in range(1, 4):
                try:
                    client.upload_chunk(filename, chunk, sent, total)
                    break
                except requests.RequestException as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status == 401:
                        # Token died mid-upload; chunks are idempotent, so
                        # re-authenticate and resend this one.
                        log(client.device.name, "token expired mid-upload, re-authenticating")
                        client.login()
                    elif attempt == 3:
                        raise
                    else:
                        time.sleep(2 * attempt)
            sent += len(chunk)
            pct = sent * 100 // total
            if pct >= next_report:
                log(client.device.name, f"upload {pct}% ({sent // (1024 * 1024)} of {total // (1024 * 1024)} MB)")
                next_report = (pct // 10 + 1) * 10


def wait_until_listed(client, filename, local_md5, verify_timeout):
    """Poll until the unit lists the image as verified, then md5sum the file
    on the unit and compare to the local md5."""
    deadline = time.time() + verify_timeout
    while time.time() < deadline:
        entry = find_listed_image(client, filename)
        if entry and entry.get("verified", "").lower() == "yes":
            device_md5 = remote_md5(client, filename)
            if device_md5 is None:
                log(client.device.name,
                    "cannot md5sum on the unit (util/bash unavailable) — "
                    "relying on the unit's own image verification")
            elif device_md5 != local_md5:
                raise RuntimeError(
                    f"md5 mismatch after upload (local {local_md5}, on-device {device_md5})"
                )
            return entry
        time.sleep(5)
    raise RuntimeError(
        f"image not verified within {verify_timeout}s — check /shared/images disk space "
        "and System >> Software Management on the unit"
    )


def push_to_device(device, image_path, local_md5, settings, force, prune, report):
    filename = os.path.basename(image_path)
    client = F5Client(device, settings["login_provider"], settings["verify_ssl"], settings["timeout"])
    try:
        client.login()
        if prune:
            prune_other_images(client, filename, report)
        existing = find_listed_image(client, filename)
        if existing and not force:
            device_md5 = remote_md5(client, filename)
            if device_md5 == local_md5:
                log(device.name, "image already present, md5sum on device matches — skipping")
                report.record(filename, device, "already-present",
                              "md5sum on device matches local image")
                return {"device": device, "status": "already-present"}
            if device_md5 is None and existing.get("verified", "").lower() == "yes":
                log(device.name, "image already listed as verified (cannot md5sum, "
                                 "util/bash unavailable) — skipping")
                report.record(filename, device, "already-present",
                              "listed as verified on device; md5sum check unavailable")
                return {"device": device, "status": "already-present"}
            log(device.name, "image name already listed but md5 differs — re-uploading")
        needed_kb = os.path.getsize(image_path) // 1024 + SPACE_HEADROOM_KB
        avail_kb = free_space_kb(client)
        if avail_kb is None:
            log(device.name, "could not check free space (util/bash unavailable) — proceeding anyway")
        elif avail_kb < needed_kb:
            raise RuntimeError(
                f"not enough space in /shared/images: {avail_kb // 1024} MB free, "
                f"need ~{needed_kb // 1024} MB — delete old images in the GUI Image List"
            )
        else:
            log(device.name, f"{avail_kb // 1024} MB free in /shared/images — enough, starting upload")
        log(device.name, f"uploading {filename} to /shared/images")
        upload_image(client, image_path, settings["chunk_size"])
        log(device.name, "upload complete, waiting for the unit to verify the checksum")
        entry = wait_until_listed(client, filename, local_md5, settings["verify_timeout"])
        version = entry.get("version", "?")
        log(device.name, f"done — version {version} verified, visible in the GUI Image List")
        report.record(filename, device, "uploaded", f"version {version}, checksum verified")
        return {"device": device, "status": "uploaded"}
    except (requests.RequestException, RuntimeError, OSError) as exc:
        log(device.name, f"FAILED: {exc}")
        report.record(filename, device, "failed", str(exc))
        return {"device": device, "status": "failed", "error": str(exc)}
    finally:
        client.logout()


def md5_of(path):
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_settings(config_path):
    if not os.path.isfile(config_path):
        sys.exit(f"config file not found: {config_path} (copy config.ini.example and fill it in)")
    parser = configparser.ConfigParser()
    parser.read(config_path)
    try:
        creds = parser["credentials"]
    except KeyError:
        sys.exit(f"{config_path} is missing the [credentials] section")
    defaults = parser["defaults"] if parser.has_section("defaults") else {}
    return {
        "username": creds.get("username", ""),
        "password": creds.get("password", ""),
        "login_provider": creds.get("login_provider", "tmos"),
        "ucs_passphrase": creds.get("ucs_passphrase", ""),
        "port": int(defaults.get("port", 443)),
        "verify_ssl": str(defaults.get("verify_ssl", "false")).strip().lower() in ("1", "true", "yes"),
        "workers": int(defaults.get("workers", 3)),
        "chunk_size": min(int(defaults.get("chunk_size_kb", 1024)) * 1024, MAX_CHUNK),
        "verify_timeout": int(defaults.get("verify_timeout", 300)),
        "timeout": int(defaults.get("request_timeout", 60)),
    }


def load_devices(csv_path, settings):
    """CSV carries only inventory (host + optional name); credentials and port
    always come from config.ini so no secrets ever live in the device list."""
    if not os.path.isfile(csv_path):
        sys.exit(f"device CSV not found: {csv_path} (copy devices.csv.example and fill it in)")
    devices = []
    with open(csv_path, newline="") as fh:
        rows = (row for row in fh if row.strip() and not row.lstrip().startswith("#"))
        for row in csv.DictReader(rows):
            host = (row.get("host") or "").strip()
            if not host:
                continue
            devices.append(Device(
                host=host,
                name=(row.get("name") or "").strip() or host,
                port=settings["port"],
                username=settings["username"],
                password=settings["password"],
            ))
    if not devices:
        sys.exit(f"no devices found in {csv_path} (needs a 'host' column)")
    return devices


def main():
    argp = argparse.ArgumentParser(description="Upload an F5 software image to one unit or a CSV fleet.")
    argp.add_argument("--image", required=True, help="path to the BIG-IP .iso to distribute")
    target = argp.add_mutually_exclusive_group(required=True)
    target.add_argument("--csv", help="device inventory CSV (host,name)")
    target.add_argument("--host", metavar="IP",
                        help="single unit to target (management IP or DNS name) instead of a CSV")
    argp.add_argument("--config", default="config.ini", help="credentials/settings INI (default: config.ini)")
    argp.add_argument("--workers", type=int, help="parallel uploads (default from config)")
    argp.add_argument("--force", action="store_true", help="re-upload even if the unit already lists the image")
    argp.add_argument("--prune", action="store_true",
                      help="before uploading, delete every other software/hotfix ISO on the unit "
                           "(only installer files are removed; installed volumes are untouched)")
    argp.add_argument("--dry-run", action="store_true", help="show what would be done without connecting")
    argp.add_argument("--report", default="push-report.csv",
                      help="append-only run history CSV (default: push-report.csv)")
    args = argp.parse_args()

    settings = load_settings(args.config)
    if args.host:
        devices = [Device(host=args.host, name=args.host, port=settings["port"],
                          username=settings["username"], password=settings["password"])]
    else:
        devices = load_devices(args.csv, settings)
    if not os.path.isfile(args.image):
        sys.exit(f"image not found: {args.image}")
    if not settings["verify_ssl"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    filename = os.path.basename(args.image)
    size_mb = os.path.getsize(args.image) // (1024 * 1024)
    workers = args.workers or settings["workers"]
    print(f"image:   {filename} ({size_mb} MB)")
    print(f"devices: {len(devices)} from {args.csv or args.host}, {workers} parallel upload(s)")

    if args.dry_run:
        extra = " (pruning other installers first)" if args.prune else ""
        for dev in devices:
            print(f"  would upload to {dev.name} ({dev.host}:{dev.port} as {dev.username}){extra}")
        return 0

    print("computing local MD5 (the unit re-checks this after upload)...")
    local_md5 = md5_of(args.image)
    print(f"md5:     {local_md5}")
    report = Report(args.report)
    print(f"report:  appending results to {args.report}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda dev: push_to_device(dev, args.image, local_md5, settings,
                                       args.force, args.prune, report),
            devices,
        ))

    print("\nsummary:")
    failed = 0
    for res in results:
        dev = res["device"]
        if res["status"] == "failed":
            failed += 1
            print(f"  FAILED           {dev.name} ({dev.host}) — {res['error']}")
        else:
            print(f"  {res['status']:<16} {dev.name} ({dev.host})")
    if failed:
        print(f"\n{failed} of {len(results)} device(s) failed")
        return 1
    print(f"\nall {len(results)} device(s) OK — image is in the GUI under "
          "System >> Software Management : Image List, ready to install")
    return 0


if __name__ == "__main__":
    sys.exit(main())
