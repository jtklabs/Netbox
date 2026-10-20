"""BIG-IP drain, persisted disabled boot state, and validated restoration."""

from .. import cancellation
import time
from copy import deepcopy
from urllib.parse import quote, urlsplit


def pages(client, path):
    seen = set()
    while path:
        parts = urlsplit(path)
        path = parts.path + ("?" + parts.query if parts.query else "")
        if not path.startswith("/mgmt/tm/") or path in seen:
            raise ValueError("invalid or repeated drain collection page")
        seen.add(path)
        page = client.get_json(path)
        if not isinstance(page, dict):
            raise ValueError("invalid drain collection response")
        yield page
        path = page.get("nextLink")


def items(client, path):
    for page in pages(client, path):
        if "items" not in page and not str(page.get("kind", "")).endswith("collectionstate"):
            raise ValueError("drain collection items are missing")
        rows = page.get("items", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("invalid drain collection items")
        yield from rows


def check_standalone(client):
    devices = list(items(client, "/mgmt/tm/cm/device"))
    if (len(devices) != 1 or str(devices[0].get("selfDevice", "")).lower() != "true"
            or str(devices[0].get("failoverState", "")).lower() != "active"):
        raise ValueError("connection draining is supported only on standalone BIG-IP units; "
                         "HA peers or a non-active/unknown failover state prevent this upgrade")
    for group in items(client, "/mgmt/tm/cm/device-group"):
        if group.get("type") == "sync-failover":
            raise ValueError("connection draining is supported only on standalone BIG-IP units; "
                             f"unit belongs to failover device group {group.get('name')}")
        if group.get("type") != "sync-only":
            raise ValueError("cannot establish standalone status: unknown device-group type")


def inventory(client):
    result = []
    for row in items(client, "/mgmt/tm/ltm/virtual"):
        name = row.get("fullPath")
        if not isinstance(name, str) or not name.startswith("/") or "~" in name:
            raise ValueError("cannot identify virtual server by fullPath for draining")
        enabled, disabled = row.get("enabled"), row.get("disabled")
        if enabled is True and disabled in (None, False):
            state = True
        elif disabled is True and enabled in (None, False):
            state = False
        else:
            raise ValueError(f"cannot determine original enabled state of {name}")
        result.append({"name": name, "enabled": state})
    if len({row["name"] for row in result}) != len(result):
        raise ValueError("duplicate virtual server in drain inventory")
    return result


def connections(client, virtuals):
    counts = {}
    for page in pages(client, "/mgmt/tm/ltm/virtual/stats"):
        entries = page.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("virtual-server connection statistics are missing")
        for entry in entries.values():
            values = entry.get("nestedStats", {}).get("entries", {})
            name = values.get("tmName", {}).get("description")
            count = values.get("clientside.curConns", {}).get("value")
            if type(count) is not int or count < 0 or not name or name in counts:
                raise ValueError("invalid virtual-server current connection statistics")
            counts[name] = count
    if set(counts) != {row["name"] for row in virtuals}:
        raise ValueError("virtual-server inventory changed or connection statistics are incomplete")
    return sum(counts.values())


def prepare(client, profile):
    check_standalone(client)
    virtuals = inventory(client)
    baseline = connections(client, virtuals) if virtuals else 0
    return {"virtual_servers": virtuals, "baseline_connections": baseline,
            "min_reduction_percent": profile.drain_min_reduction_percent,
            "window_seconds": profile.drain_window_seconds, "timeout_seconds": profile.drain_timeout_seconds,
            "restore_pending": [], "status": "planned"}


def set_enabled(client, name, enabled):
    path = "/mgmt/tm/ltm/virtual/" + quote(name.replace("/", "~"), safe="~")
    client.request("PATCH", path, {"enabled" if enabled else "disabled": True})


def restore(client, record, emit):
    failures = []
    for name in list(record["restore_pending"]):
        try:
            set_enabled(client, name, True)
            record["restore_pending"].remove(name)
        except Exception:
            failures.append(name)
    emit("restoring_virtuals", "Virtual-server state restoration " + ("failed" if failures else "complete"),
         {"connection_drain": record})
    if failures:
        raise RuntimeError("manual VIP restoration required for: " + ", ".join(failures))


def persist_disabled(client, record, emit):
    record['disabled_config_saved'] = True  # Save responses can be lost.
    client.save_config()
    emit('draining', 'Saved disabled virtual-server configuration for installation and boot',
         {'connection_drain': record})


def hold_after_reboot(client, record, emit):
    current = {row['name']: row for row in inventory(client)}
    if set(current) != {row['name'] for row in record['virtual_servers']}:
        raise RuntimeError('Virtual-server inventory changed during reboot; recovery required')
    if any(current[name]['enabled'] for name in record['restore_pending']):
        disable_again(client, record, emit)
        raise RuntimeError('A drained virtual server booted enabled; disabled again for recovery')
    emit('converging', 'Virtual servers booted disabled; waiting for WAF and health validation',
         {'connection_drain': record})


def disable_again(client, record, emit):
    record['restore_pending'] = [v['name'] for v in record['virtual_servers'] if v['enabled']]
    failures = []
    for name in record['restore_pending']:
        try:
            set_enabled(client, name, False)
        except Exception:
            failures.append(name)
    persist_disabled(client, record, emit)
    if failures:
        raise RuntimeError('Could not re-disable virtual servers; manual recovery required for: ' + ', '.join(failures))


def compare_while_drained(before, after, record, skip_running_config_check=False):
    """Account only for the deliberate admin-state change; retain raw evidence."""
    from . import checks
    comparable = deepcopy(after)
    pending = set(record['restore_pending'])
    for row in comparable.get('tables', {}).get('virtual_servers', []):
        if row.get('name') in pending and row.get('enabled') in {'disabled', 'disabled-by-user'}:
            row['enabled'] = 'enabled'
    findings = checks.compare(before, comparable, skip_running_config_check=skip_running_config_check)
    observed = {r.get('name'): r.get('enabled') for r in after.get('tables', {}).get('virtual_servers', [])}
    if any(observed.get(name) not in {'disabled', 'disabled-by-user'} for name in pending):
        findings.append({'check': 'traffic_hold', 'severity': 'error', 'message': 'A drained virtual server is not disabled'})
    return findings


def release_traffic(client, record, emit):
    restore(client, record, emit)
    if record.get('disabled_config_saved'):
        client.save_config()
        record['disabled_config_saved'] = False
        emit('restoring_virtuals', 'Original virtual-server states restored and saved', {'connection_drain': record})


def drain(client, profile, record, emit):
    # Refresh immediately before mutation; inventory may have changed since prechecks.
    fresh = prepare(client, profile)
    record.clear()
    record.update(fresh)
    record["status"] = "draining"
    if not emit("draining", "Disabling virtual servers and observing current client connections",
                {"connection_drain": record}):
        raise ValueError("progress webhook unavailable before disabling virtual servers")
    for row in record["virtual_servers"]:
        if row["enabled"]:
            # Include uncertain PATCH outcomes in restoration, too.
            record["restore_pending"].append(row["name"])
            set_enabled(client, row["name"], False)
    start = window_start = time.monotonic()
    previous = record["baseline_connections"]
    while True:
        current = connections(client, record["virtual_servers"]) if record["virtual_servers"] else 0
        now = time.monotonic()
        record.update(current_connections=current, elapsed_seconds=round(now - start, 1))
        reason = "zero_connections" if current == 0 else None
        if now - window_start >= profile.drain_window_seconds:
            reduction = 100 * (previous - current) / previous if previous else 0
            record["window_reduction_percent"] = round(reduction, 2)
            # Rising traffic is not a plateau; let the maximum wait abort it.
            if previous > 0 and 0 <= reduction < profile.drain_min_reduction_percent:
                reason = reason or "plateau"
            previous, window_start = current, now
        if reason:
            record.update(status="complete", reason=reason)
        elif now - start >= profile.drain_timeout_seconds:
            record.update(status="timed_out", reason="maximum_wait")
        emit("draining", f"{current} client connections remain after {record['elapsed_seconds']}s; "
             f"{record['status']}", {"connection_drain": record})
        if reason:
            return
        if record["status"] == "timed_out":
            raise TimeoutError("connection drain did not settle before the maximum wait; reboot cancelled")
        _sleep(min(5, profile.drain_window_seconds - (now - window_start),
                       profile.drain_timeout_seconds - (now - start)))


def _sleep(seconds):
    return cancellation.sleep(seconds, fallback=time.sleep)
