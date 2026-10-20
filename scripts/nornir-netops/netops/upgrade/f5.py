"""BIG-IP iControl REST operations for staging and installing software.

Migrated from scripts/f5-image-push: the chunked upload into /shared/images,
the free-space and md5 checks through util/bash, image pruning, and the UCS
save/download used as the pre-upgrade backup. Everything here works on an
open netops.f5_waf.Client. Baseline collection reads configuration and status
collections and keeps only stable fields, so the comparison is unaffected by
row order or counters.
"""

import os
import re
import shlex
import time
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

from .. import cancellation
from .images import download, local_md5  # noqa: F401  (BIG-IP callers use f5.download/f5.local_md5)

MAX_CHUNK = 1024 * 1024        # iControl REST rejects larger upload chunks.
TOKEN_TIMEOUT = 36000          # The 10-hour maximum, so slow uploads outlive the default 20 minutes.
IMAGES = "/shared/images"
UCS_DIR = "/var/local/ucs"


def _raw(client, method, path, **kwargs):
    cancellation.checkpoint()
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
        # Cold-cache multi-GB images can exceed the normal REST read timeout.
        out = bash(client, f"md5sum '{directory}/{filename}'", timeout=max(client.timeout, 120)).split()
    except RuntimeError:
        return None
    return out[0].lower() if out and re.fullmatch(r"[0-9a-fA-F]{32}", out[0]) else None


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
                _sleep(2 * attempt)
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
        _sleep(poll)


def prune_images(client, keep, required_free_bytes):
    """Remove uploaded installers only until enough space is available."""
    removed, failed = [], []
    for row in listed_images(client):
        name = row.get("name", "")
        if not name or name == keep:
            continue
        free = free_space_kb(client)
        if free is None:
            break
        if free * 1024 >= required_free_bytes:
            break
        try:
            client.request("DELETE", f"/mgmt/tm/sys/software/{row['kind']}/{quote(name, safe='')}", timeout=max(client.timeout, 120))
            removed.append(name)
        except (RuntimeError, ValueError):
            failed.append(name)
    return removed, failed


def save_ucs(client, name, passphrase="", timeout=1800, poll=5, progress=lambda message: None):
    """Launch once outside REST's short server deadline and verify save exit status."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.ucs", name):
        raise ValueError("invalid UCS archive name")
    directory = f"/var/tmp/netops-ucs-{uuid.uuid4().hex}"
    log = directory + "/output.log"
    tmsh_command = f"save sys ucs {name}"
    if passphrase:
        # tmsh parses its own quoted arguments, then bash parses the command.
        escaped = passphrase.replace('\\', '\\\\').replace('"', '\\"')
        if any(ord(c) < 32 for c in passphrase):
            raise ValueError('UCS passphrase must not contain control characters')
        tmsh_command += ' passphrase "' + escaped + '"'
    command = 'tmsh -c ' + shlex.quote(tmsh_command)
    script = (f"flock -n /var/run/netops-ucs.lock /bin/bash -c {shlex.quote(command)}; "
              f"rc=$?; printf '%s\\n' \"$rc\" > {directory}/status.tmp; "
              f"mv {directory}/status.tmp {directory}/status")
    launch = (f"umask 077; mkdir {directory} || exit 1; "
              f"nohup /bin/bash -c {shlex.quote(script)} </dev/null >{log} 2>&1 & "
              "printf 'NETOPS_UCS_STARTED\\n'")
    query = (f"if test -f {directory}/status; then printf 'NETOPS_UCS_EXIT='; cat {directory}/status; "
             f"elif test -d {directory}; then printf 'NETOPS_UCS_RUNNING\\n'; "
             "else printf 'NETOPS_UCS_MISSING\\n'; fi")
    def run(command):
        result = client.request('POST', '/mgmt/tm/util/bash',
                                {'command': 'run', 'utilCmdArgs': '-c ' + shlex.quote(command)})
        return str(result.get('commandResult', '')).strip()
    deadline = time.monotonic() + timeout
    # Never repeat a launch after an ambiguous response.
    if run(launch) != 'NETOPS_UCS_STARTED':
        raise RuntimeError(f'UCS launch not confirmed; inspect private log {log}')
    while True:
        output = run(query)
        if output == 'NETOPS_UCS_EXIT=0':
            return
        if output != 'NETOPS_UCS_RUNNING':
            raise RuntimeError(f'UCS save failed or completion is unknown; inspect private log {log}')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'UCS save deadline exceeded; it may still be running; inspect private log {log}')
        progress('UCS save is running; waiting for confirmed completion')
        _sleep(min(max(poll, 1), remaining))


def download_ucs(client, name, destination):
    """Chunked ranged download of a saved UCS; returns the bytes written."""
    path = "/mgmt/shared/file-transfer/ucs-downloads/" + quote(name)

    def get_range(start, end, total):
        response = _raw(client, "GET", path, headers={"Content-Type": "application/octet-stream",
                                                       "Content-Range": f"{start}-{end}/{total}"})
        if response.status_code >= 400:
            raise RuntimeError(f"UCS download returned HTTP {response.status_code}")
        return response

    def checked_range(response, start, end, total=None):
        match = re.fullmatch(r"(?:bytes )?(\d+)-(\d+)/(\d+)",
                             str(response.headers.get("Content-Range", "")))
        if not match:
            raise RuntimeError("UCS download has no valid Content-Range")
        first, last, size = map(int, match.groups())
        if (size <= 0 or first != start or last != end or last >= size
                or (total is not None and size != total) or len(response.content) != end - start + 1):
            raise RuntimeError("UCS download returned an incomplete or inconsistent byte range")
        return size

    total = checked_range(get_range(0, 0, 0), 0, 0)
    written = 0
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        # Private from creation, even if the process has a permissive umask.
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".ucs-", delete=False) as handle:
            temporary = Path(handle.name)
            while written < total:
                end = min(written + MAX_CHUNK, total) - 1
                response = get_range(written, end, total)
                checked_range(response, written, end, total)
                handle.write(response.content)
                written += len(response.content)
            handle.flush()
            os.fsync(handle.fileno())
        # Never overwrite a prior recovery archive.
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return written


# --- installation ----------------------------------------------------------

def install(client, image, volume, create):
    payload = {"command": "install", "name": image, "volume": volume}
    if create:
        payload["options"] = [{"create-volume": True}]
    result = client.request("POST", "/mgmt/tm/sys/software/image", payload, timeout=max(client.timeout, 120))
    if str(result.get("commandResult", "")).strip():
        raise RuntimeError(f"software install command failed: {result['commandResult']}")


def volume_status(client, volume):
    return str(client.get_json("/mgmt/tm/sys/software/volume/" + quote(volume)).get("status", ""))


def volume_info(client, volume):
    # A newly created volume may not yet have a resource endpoint.
    return next((v for v in client.get_json("/mgmt/tm/sys/software/volume").get("items", [])
                 if v.get("name") == volume), {})


def copy_configuration(client, source, destination, timeout, poll=5, progress=lambda message: None):
    """Copy saved configuration to the inactive target; require cpcfg exit zero."""
    if (source == destination or any(not re.fullmatch(r"HD\d+\.\d+", str(v))
                                     for v in (source, destination))):
        raise ValueError("configuration copy requires distinct valid boot volumes")
    volumes = client.get_json("/mgmt/tm/sys/software/volume").get("items", [])
    origin = next((v for v in volumes if v.get("name") == source), {})
    target = next((v for v in volumes if v.get("name") == destination), {})
    if (str(origin.get("active")).lower() != "true" or str(target.get("active", False)).lower() != "false"
            or str(target.get("status", "")).lower() != "complete"):
        raise ValueError("configuration copy requires the original active source and a complete inactive target")
    # REST's own AsyncContext deadline is shorter than a large cpcfg run.
    # Launch once, detach all streams, then poll an atomic exit-code file.
    directory = f"/var/tmp/netops-cpcfg-{uuid.uuid4().hex}"
    log = directory + "/output.log"
    script = (f"flock -n /var/run/netops-cpcfg.lock /sbin/cpcfg --source={source} {destination}; "
              f"rc=$?; printf '%s\\n' \"$rc\" > {directory}/status.tmp; "
              f"mv {directory}/status.tmp {directory}/status")
    command = (f"umask 077; mkdir {directory} || exit 1; "
               f"nohup /bin/bash -c {shlex.quote(script)} </dev/null >{log} 2>&1 & "
               "printf 'NETOPS_CPCFG_STARTED\\n'")

    def run_script(command):
        result = client.request("POST", "/mgmt/tm/util/bash",
                                {"command": "run", "utilCmdArgs": "-c " + shlex.quote(command)})
        return str(result.get("commandResult", "")).strip()

    deadline = time.monotonic() + timeout
    progress(f"Starting configuration copy to {destination}; private log on unit: {log}")
    # An unacknowledged launch is never resent. Recovery can inspect this log.
    if run_script(command) != "NETOPS_CPCFG_STARTED":
        raise RuntimeError(f"configuration copy launch was not confirmed; reboot cancelled; inspect {log}")
    query = (f"if test -f {directory}/status; then printf 'NETOPS_CPCFG_EXIT='; cat {directory}/status; "
             f"elif test -d {directory}; then printf 'NETOPS_CPCFG_RUNNING\\n'; "
             "else printf 'NETOPS_CPCFG_MISSING\\n'; fi")
    while True:
        output = run_script(query)
        match = re.fullmatch(r"NETOPS_CPCFG_EXIT=(\d+)", output)
        if match and match[1] == "0":
            return {"source": source, "destination": destination, "verified": True, "log_path": log}
        if output != "NETOPS_CPCFG_RUNNING":
            raise RuntimeError(f"configuration copy to {destination} failed or completion was not confirmed; "
                               f"reboot cancelled; inspect {log}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"configuration copy to {destination} exceeded {int(timeout)}s; "
                               f"it may still be running, reboot cancelled; inspect {log}")
        progress(f"Configuration copy to {destination} still running; waiting for confirmed completion")
        _sleep(min(max(poll, 1), remaining))


def reboot_to(client, volume):
    import requests
    try:
        result = client.request("POST", "/mgmt/tm/sys", {"command": "reboot", "volume": volume},
                                timeout=max(client.timeout, 60))
    except (requests.ConnectionError, requests.Timeout):
        # Only transport loss is ambiguous. HTTP rejections must fail immediately.
        # Never resend a reboot whose outcome is unknown.
        return False
    if str(result.get("commandResult", "")).strip():
        raise RuntimeError(f"reboot command failed: {result['commandResult']}")
    return True


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
    license_document = get("/mgmt/tm/sys/license")
    license_entries = first_entry(license_document)
    license_unavailable = (str(license_document.get("apiRawValues", {}).get("apiAnonymous", "")).strip()
                           == "Can't load license, may not be operational")
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
        "license_unavailable": license_unavailable,
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


ASM_POLICIES = "/mgmt/tm/asm/policies"


def waf_policies(get):
    """Read every page; an unavailable or partial collection is never an empty baseline."""
    path, seen, rows, expected = ASM_POLICIES, set(), [], None
    while path:
        if path in seen or len(seen) >= 10000:
            raise ValueError("WAF policy pagination did not finish")
        seen.add(path)
        document = get(path)
        items = document.get("items")
        if not isinstance(items, list):
            raise ValueError("WAF policy response is missing its items list")
        if "totalItems" in document:
            total = document["totalItems"]
            if not isinstance(total, int) or total < 0 or (expected is not None and total != expected):
                raise ValueError("WAF policy total changed during collection")
            expected = total
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("WAF policy response contains a non-object item")
            # All policy types count, including parents without enforcement fields.
            # Metadata is informational; comparison uses only the collection size.
            rows.append({key: item[key] for key in ("fullPath", "type", "active", "enforcementMode") if key in item})
        link = document.get("nextLink")
        if link:
            parts = urlsplit(link)
            if parts.path != ASM_POLICIES or parts.fragment:
                raise ValueError("Unexpected WAF policy pagination path")
            # Only pass a relative path to our authenticated client; never follow a supplied host.
            path = parts.path + ("?" + parts.query if parts.query else "")
        else:
            path = None
    if expected is not None and len(rows) != expected:
        raise ValueError(f"WAF policy collection incomplete: {len(rows)} of {expected}")
    return rows


def collect_waf(client, get, snapshot):
    snapshot["waf"] = {"provisioned": True, "ready": None, "policy_count": None}
    try:
        policies = waf_policies(get)
        snapshot["tables"]["waf_policies"] = policies
        snapshot["waf"]["policy_count"] = len(policies)
    except Exception as exc:
        snapshot["errors"]["waf_policies"] = str(exc)
    try:
        # F5's ASM readiness signal, rather than REST availability or inventory alone.
        output = bash(client, "perl -MF5::ASMReady -e 'print F5::ASMReady::is_asm_ready() ? 1 : 0'").strip()
        if output not in {"0", "1"}:
            raise ValueError("ASM readiness signal is unavailable")
        snapshot["waf"]["ready"] = output == "1"
        if output != "1":
            snapshot["errors"]["waf_ready"] = "ASM is still loading; readiness signal is false"
    except Exception as exc:
        snapshot["errors"]["waf_ready"] = str(exc)


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
            if key == "nodes":
                snapshot["tables"][key] = [row for row in snapshot["tables"][key]
                                          if "_auto" not in str(row.get("name", ""))]
        except Exception as exc:
            snapshot["errors" if (ltm or not needs_ltm) else "warnings"][key] = str(exc)
    if any(row.get("name") == "asm" and row.get("level") not in (None, "none")
           for row in snapshot["tables"].get("provision", [])):
        collect_waf(client, get, snapshot)
    snapshot["metrics"] = {"counts": {key: len(rows) for key, rows in snapshot["tables"].items()}}
    info = snapshot.get("facts", {})
    snapshot["health"] = {"alarms": [], "interface_errors": {}, "failover_state": info.get("failover_state"),
                          "sync_status": info.get("sync_status"), "license_service_check_date": info.get("license_service_check_date")}
    return snapshot


def _sleep(seconds):
    return cancellation.sleep(seconds, fallback=time.sleep)
