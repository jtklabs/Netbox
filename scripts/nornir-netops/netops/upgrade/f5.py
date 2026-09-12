"""BIG-IP iControl REST operations for staging and installing software.

Migrated from scripts/f5-image-push: the chunked upload into /shared/images,
the free-space and md5 checks through util/bash, image pruning, and the UCS
save/download used as the pre-upgrade backup. Everything here works on an
open netops.f5_waf.Client. Baseline collection reads configuration and status
collections and keeps only stable fields, so the comparison is unaffected by
row order or counters.
"""

import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

MAX_CHUNK = 1024 * 1024        # iControl REST rejects larger upload chunks.
TOKEN_TIMEOUT = 36000          # The 10-hour maximum, so slow uploads outlive the default 20 minutes.
IMAGES = "/shared/images"
UCS_DIR = "/var/local/ucs"


def _raw(client, method, path, **kwargs):
    import requests
    try:
        return client.session.request(method, client.base + path, verify=client.verify_tls,
                                      timeout=kwargs.pop("timeout", max(client.timeout, 120)), **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError(f"F5 {method} {path}: connection failed ({type(exc).__name__})") from None


def extend_token(client):
    try:
        _raw(client, "PATCH", "/mgmt/shared/authz/tokens/" + quote(str(client.token), safe=""),
             json={"timeout": TOKEN_TIMEOUT}, timeout=client.timeout)
    except RuntimeError:
        pass


def relogin(client):
    client.__enter__()


def bash(client, command, timeout=None):
    result = client.request("POST", "/mgmt/tm/util/bash",
                            {"command": "run", "utilCmdArgs": f'-c "{command}"'}, timeout=timeout)
    return str(result.get("commandResult", ""))


def free_space_kb(client):
    """Available KB on the filesystem holding /shared/images, or None when util/bash is unavailable."""
    try:
        return int(bash(client, f"df -Pk {IMAGES} | tail -1").split()[3])
    except (RuntimeError, ValueError, IndexError):
        return None


def listed_images(client):
    rows = []
    for kind, path in (("image", "/mgmt/tm/sys/software/image"), ("hotfix", "/mgmt/tm/sys/software/hotfix")):
        for item in client.get_json(path).get("items", []):
            rows.append(dict(item, kind=kind))
    return rows


def find_image(client, filename):
    return next((row for row in listed_images(client) if row.get("name") == filename), None)


def remote_md5(client, filename, directory=IMAGES):
    """md5sum of a file on the unit, or None when util/bash is unavailable.

    The `checksum` field on sys/software/image is not the md5 of the ISO, so
    it is never compared against the profile.
    """
    try:
        out = bash(client, f"md5sum '{directory}/{filename}'").split()
    except RuntimeError:
        return None
    return out[0].lower() if out and re.fullmatch(r"[0-9a-fA-F]{32}", out[0]) else None


def local_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, destination):
    """Stream an image from the distribution server into the worker cache."""
    import requests
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(destination.name + ".part")
    try:
        with requests.get(url, stream=True, timeout=120, allow_redirects=False) as response:
            if response.status_code != 200:
                raise RuntimeError(f"image download returned HTTP {response.status_code}")
            with open(temporary, "wb") as handle:
                for block in response.iter_content(chunk_size=4 * 1024 * 1024):
                    handle.write(block)
        os.replace(temporary, destination)
    except requests.RequestException as exc:
        raise RuntimeError(f"image download failed ({type(exc).__name__})") from None
    finally:
        if temporary.exists():
            temporary.unlink()


def upload_image(client, path, progress=lambda percent: None):
    filename, total, sent, next_report = Path(path).name, os.path.getsize(path), 0, 10
    with open(path, "rb") as handle:
        while sent < total:
            chunk = handle.read(MAX_CHUNK)
            for attempt in range(1, 4):
                response = _raw(client, "POST", "/mgmt/cm/autodeploy/software-image-uploads/" + quote(filename),
                                headers={"Content-Type": "application/octet-stream",
                                         "Content-Range": f"{sent}-{sent + len(chunk) - 1}/{total}"},
                                data=chunk)
                if response.status_code == 401 and attempt < 3:
                    # The token died mid-upload; chunks are idempotent.
                    relogin(client)
                    continue
                if response.status_code < 400:
                    break
                if attempt == 3:
                    raise RuntimeError(f"image upload failed (HTTP {response.status_code}) at byte {sent}")
                time.sleep(2 * attempt)
            sent += len(chunk)
            percent = sent * 100 // total
            if percent >= next_report:
                progress(percent)
                next_report = (percent // 10 + 1) * 10


def wait_verified(client, filename, expected_md5, timeout, poll=5):
    """Wait until the unit lists the image as verified, then md5sum it on the unit."""
    deadline = time.monotonic() + timeout
    while True:
        entry = find_image(client, filename)
        if entry and str(entry.get("verified", "")).lower() == "yes":
            digest = remote_md5(client, filename)
            if digest is None:
                return entry, "verified by the unit; md5sum unavailable because util/bash is disabled"
            if digest != expected_md5.lower():
                raise ValueError("image checksum on the unit does not match the approved profile")
            return entry, "md5 verified on the unit"
        if time.monotonic() >= deadline:
            raise TimeoutError(f"image not verified by the unit within {int(timeout)}s")
        time.sleep(poll)


def prune_images(client, keep):
    """Delete every other installer ISO; installed volumes are never touched."""
    removed, failed = [], []
    for row in listed_images(client):
        name = row.get("name", "")
        if not name or name == keep:
            continue
        try:
            client.request("DELETE", f"/mgmt/tm/sys/software/{row['kind']}/{quote(name)}", timeout=max(client.timeout, 120))
            removed.append(name)
        except (RuntimeError, ValueError):
            failed.append(name)
    return removed, failed


def save_ucs(client, name, passphrase=""):
    payload = {"command": "save", "name": name}
    if passphrase:
        payload["options"] = [{"passphrase": passphrase}]
    client.request("POST", "/mgmt/tm/sys/ucs", payload, timeout=600)


def download_ucs(client, name, destination):
    """Chunked ranged download of a saved UCS; returns the bytes written."""
    path = "/mgmt/shared/file-transfer/ucs-downloads/" + quote(name)

    def get_range(start, end, total):
        response = _raw(client, "GET", path, headers={"Content-Type": "application/octet-stream",
                                                       "Content-Range": f"{start}-{end}/{total}"})
        if response.status_code >= 400:
            raise RuntimeError(f"UCS download returned HTTP {response.status_code}")
        return response

    probe = get_range(0, 0, 0)
    total = int(str(probe.headers.get("Content-Range", "0-0/0")).split("/")[-1])
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(destination, "wb") as handle:
        while written < total:
            response = get_range(written, min(written + MAX_CHUNK, total) - 1, total)
            handle.write(response.content)
            if not response.content:
                raise RuntimeError("UCS download stalled")
            written += len(response.content)
    os.chmod(destination, 0o600)
    return written


# --- installation ----------------------------------------------------------

def install(client, image, volume, create):
    payload = {"command": "install", "name": image, "volume": volume}
    if create:
        payload["options"] = [{"create-volume": True}]
    client.request("POST", "/mgmt/tm/sys/software/image", payload, timeout=max(client.timeout, 120))


def volume_status(client, volume):
    return str(client.get_json("/mgmt/tm/sys/software/volume/" + quote(volume)).get("status", ""))


def reboot_to(client, volume):
    try:
        client.request("POST", "/mgmt/tm/sys", {"command": "reboot", "options": [{"volume": volume}]},
                       timeout=max(client.timeout, 60))
    except RuntimeError:
        # The management plane drops as the unit reboots; the outcome is
        # established by reconnecting, never by resending the reboot.
        pass


def failover_to_standby(client):
    client.request("POST", "/mgmt/tm/sys/failover", {"command": "run", "standby": True},
                   timeout=max(client.timeout, 60))


# --- facts and baseline ------------------------------------------------------

def first_entry(document):
    return next(iter(document.get("entries", {}).values()), {}).get("nestedStats", {}).get("entries", {})


def describe(cell):
    if isinstance(cell, dict):
        return cell.get("description", cell.get("value"))
    return cell


def facts(get):
    devices = get("/mgmt/tm/cm/device").get("items", [])
    me = next((d for d in devices if str(d.get("selfDevice", "")).lower() == "true"), None)
    if me is None:
        me = devices[0] if len(devices) == 1 else {}
    peers = sorted(str(d.get("name")) for d in devices if d is not me)
    volumes = get("/mgmt/tm/sys/software/volume").get("items", [])
    active = next((v for v in volumes if str(v.get("active", "")).lower() == "true"), {})
    sync = first_entry(get("/mgmt/tm/cm/sync-status"))
    license_entries = first_entry(get("/mgmt/tm/sys/license"))
    system = first_entry(get("/mgmt/tm/sys/version"))
    check = describe(license_entries.get("serviceCheckDate"))
    check = check.replace("/", "-") if isinstance(check, str) else None
    return {
        "hostname": me.get("hostname"), "model": me.get("marketingName"), "platform_id": me.get("platformId"),
        "chassis_id": me.get("chassisId"), "version": me.get("version") or describe(system.get("Version")),
        "build": me.get("build") or describe(system.get("Build")), "product": describe(system.get("Product")),
        "failover_state": str(me.get("failoverState", "")).lower() or None, "peers": peers,
        "volumes": [{k: v.get(k) for k in ("name", "active", "version", "build", "status", "product")} for v in volumes],
        "active_volume": active.get("name"), "active_status": active.get("status"),
        "sync_status": describe(sync.get("status")), "sync_color": describe(sync.get("color")),
        "license_service_check_date": check,
    }


def rows_from_stats(document, mapping):
    rows = []
    for entry in document.get("entries", {}).values():
        values = entry.get("nestedStats", {}).get("entries", {})
        rows.append({key: describe(values.get(source)) for key, source in mapping.items()})
    return rows


def rows_from_items(document, fields):
    return [{key: item.get(key) for key in fields} for item in document.get("items", [])]


# key: (path, mapping, requires LTM)
STATS = {
    "virtual_servers": ("/mgmt/tm/ltm/virtual/stats", {"name": "tmName", "availability": "status.availabilityState", "enabled": "status.enabledState"}, True),
    "pools": ("/mgmt/tm/ltm/pool/stats", {"name": "tmName", "availability": "status.availabilityState", "enabled": "status.enabledState", "active_members": "activeMemberCnt"}, True),
    "nodes": ("/mgmt/tm/ltm/node/stats", {"name": "tmName", "availability": "status.availabilityState", "enabled": "status.enabledState"}, True),
    "interfaces": ("/mgmt/tm/net/interface/stats", {"name": "tmName", "status": "status"}, False),
}
# key: (path, fields, required)
CONFIG = {
    "provision": ("/mgmt/tm/sys/provision", ("name", "level"), True),
    "vlans": ("/mgmt/tm/net/vlan", ("name", "tag"), True),
    "self_ips": ("/mgmt/tm/net/self", ("name", "address", "vlan"), True),
    "routes": ("/mgmt/tm/net/route", ("name", "network", "gw"), True),
    "trunks": ("/mgmt/tm/net/trunk", ("name", "interfaces"), False),
    "irules": ("/mgmt/tm/ltm/rule", ("name",), False),
    "certificates": ("/mgmt/tm/sys/file/ssl-cert", ("name", "expirationString"), False),
}


def collect(client, progress):
    snapshot = {"raw": {}, "tables": {}, "errors": {}, "warnings": {}, "routing": {}, "routing_commands": {}}

    def get(path):
        progress(path)
        document = client.get_json(path)
        snapshot["raw"][path] = document
        return document

    try:
        info = facts(get)
        snapshot["facts"] = info
        snapshot["software"] = {"1": {"model": info["model"], "version": info["version"], "mode": info["active_volume"]}}
        # Failover state moves during an HA upgrade by design; it is compared in target_findings.
        snapshot["stack"] = {"1": {"mac": info["chassis_id"] or info["hostname"] or "unit", "state": "ready"}}
    except Exception as exc:
        snapshot["errors"]["facts"] = str(exc)
    ltm = False
    for key, (path, fields, required) in CONFIG.items():
        try:
            snapshot["tables"][key] = rows_from_items(get(path), fields)
            if key == "provision":
                ltm = any(row.get("name") == "ltm" and row.get("level") not in (None, "none") for row in snapshot["tables"][key])
        except Exception as exc:
            snapshot["errors" if required else "warnings"][key] = str(exc)
    for key, (path, mapping, needs_ltm) in STATS.items():
        try:
            snapshot["tables"][key] = rows_from_stats(get(path), mapping)
        except Exception as exc:
            snapshot["errors" if (ltm or not needs_ltm) else "warnings"][key] = str(exc)
    snapshot["metrics"] = {"counts": {key: len(rows) for key, rows in snapshot["tables"].items()}}
    info = snapshot.get("facts", {})
    snapshot["health"] = {"alarms": [], "interface_errors": {}, "failover_state": info.get("failover_state"),
                          "sync_status": info.get("sync_status"), "license_service_check_date": info.get("license_service_check_date")}
    return snapshot
