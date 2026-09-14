"""Persist interface NAC findings using the shared NetBox client."""

from __future__ import annotations

import ipaddress
import os
import socket
from datetime import datetime, timezone

from .debuglog import redact
from .netbox import NetBoxError


CHOICE_NAME = "NAC compliance status"
CHOICES = [["compliant", "Compliant"], ["noncompliant", "Noncompliant"],
           ["skipped", "Skipped"], ["unknown", "Unknown"]]
FIELDS = {
    "nac_status": ("select", "NAC status", "Latest NAC assessment; unknown is not a passing result."),
    "nac_issues": ("longtext", "NAC findings", "Missing required commands, exclusion reason, or audit failure."),
    "nac_remediation": ("longtext", "NAC remediation", "Commands or next steps needed to resolve the findings."),
    "nac_checked_at": ("datetime", "NAC last assessed", "Last time this interface was successfully assessed, including scope exclusions."),
    "nac_last_attempt": ("datetime", "NAC last attempted", "Latest audit reporting attempt, including unknown results."),
    "nac_poller": ("text", "NAC poller", "Poller tag or host which reported the latest result."),
}


def enabled(args):
    value = args.sync_netbox
    if value is not None:
        return value
    setting = os.environ.get("NETOPS_NAC_NETBOX_SYNC", "false").strip().lower()
    if setting not in ("true", "false"):
        raise NetBoxError("NETOPS_NAC_NETBOX_SYNC must be true or false")
    return setting == "true"


def _value(value):
    return value.get("value") if isinstance(value, dict) else value


def _one(rows, name):
    if len(rows) != 1 or rows[0].get("name") != name:
        raise NetBoxError(f"ambiguous NetBox lookup for {name}")
    return rows[0]


def ensure_fields(client):
    """Validate existing definitions before creating any missing fields."""
    existing = {}
    for name, (kind, _, _) in FIELDS.items():
        rows = client.get("extras/custom-fields/", {"name": name})
        if not rows:
            continue
        field = _one(rows, name)
        if _value(field.get("type")) != kind or "dcim.interface" not in field.get("object_types", []):
            raise NetBoxError(f"{name} must be a {kind} custom field assigned to dcim.interface")
        existing[name] = field

    status = existing.get("nac_status")
    if status:
        ref = status.get("choice_set")
        choice_id = ref.get("id") if isinstance(ref, dict) else ref
        if not choice_id:
            raise NetBoxError("nac_status requires a choice set")
        choices = client.request_object("GET", f"extras/custom-field-choice-sets/{int(choice_id)}/")
    else:
        rows = client.get("extras/custom-field-choice-sets/", {"name": CHOICE_NAME})
        choices = _one(rows, CHOICE_NAME) if rows else None
    if choices and not {item[0] for item in CHOICES}.issubset(
            {item[0] for item in choices.get("extra_choices", [])}):
        raise NetBoxError("NAC status choice set must include compliant, noncompliant, skipped and unknown")
    if choices is None:
        choices = client.request_object("POST", "extras/custom-field-choice-sets/", {
            "name": CHOICE_NAME, "extra_choices": CHOICES,
        })
    for name, (kind, label, description) in FIELDS.items():
        if name in existing:
            continue
        definition = {
            "name": name, "type": kind, "label": label, "description": description,
            "object_types": ["dcim.interface"], "required": False,
            "group_name": "NAC compliance", "is_cloneable": False,
        }
        if name == "nac_status":
            definition.update(choice_set=choices["id"], default="unknown", filter_logic="exact")
        client.request_object("POST", "extras/custom-fields/", definition)


def device_id(client, host):
    """Use inventory identity; CSV/IP runs require an unambiguous IPAM mapping."""
    known = host.data.get("netbox_id")
    if known:
        return int(known)
    try:
        address = ipaddress.ip_address(host.hostname)
    except ValueError:
        rows = client.get("dcim/devices/", {"name": host.name})
        return int(_one(rows, host.name)["id"])
    candidates = set()
    for item in client.get("ipam/ip-addresses/", {"address": str(address)}):
        if ipaddress.ip_interface(item["address"]).ip != address:
            continue
        if item.get("assigned_object_type") != "dcim.interface":
            continue
        assigned = item.get("assigned_object") or {}
        device = assigned.get("device") or {}
        if not device.get("id") and item.get("assigned_object_id"):
            interface = client.request_object("GET", f"dcim/interfaces/{int(item['assigned_object_id'])}/")
            device = interface.get("device") or {}
        if device.get("id"):
            candidates.add(int(device["id"]))
    if len(candidates) != 1:
        raise NetBoxError(f"{host.name}: expected one NetBox device assigned to {address}; found {len(candidates)}")
    return candidates.pop()


def snapshot(record, applying):
    """Return observed state, never a prediction that an attempted push worked."""
    if record.get("audit_after") is not None:
        return record["audit_after"], None
    if applying and record.get("commands"):
        return None, "Configuration changes were attempted without a completed read-back audit."
    if record.get("audit_before") is not None:
        return record["audit_before"], None
    return None, record.get("error") or "No completed NAC audit is available."


def fields_for(port, attempted_at, poller, reason=None, checked_at=None):
    fields = {"nac_last_attempt": attempted_at, "nac_poller": poller}
    if port is None:
        fields.update(nac_status="unknown", nac_issues=reason or "Interface was not present in the collected configuration.",
                      nac_remediation="Resolve the collection or inventory issue and rerun the NAC audit before changing configuration.")
    else:
        status = port["status"]
        if status not in {"compliant", "noncompliant", "skipped"}:
            raise NetBoxError(f"invalid NAC interface status {status!r}")
        fields.update(nac_status=status, nac_checked_at=checked_at or attempted_at)
        missing = port.get("missing_lines") or []
        if status == "noncompliant":
            if not missing:
                raise NetBoxError("noncompliant NAC interface has no findings")
            fields["nac_issues"] = "Missing required configuration:\n" + "\n".join(f"- `{line}`" for line in missing)
            commands = [f"interface {port['name']}", *[f" {line}" for line in missing]]
            fields["nac_remediation"] = "Apply the missing interface configuration:\n\n```text\n" + "\n".join(commands) + "\n```"
        elif status == "skipped":
            fields.update(nac_issues=port.get("skip_reason") or "Outside NAC audit scope.",
                          nac_remediation="No NAC change proposed; review the scope exclusion if this interface should be audited.")
        else:
            fields.update(nac_issues="", nac_remediation="")
    return {key: redact(value) if key in ("nac_issues", "nac_remediation") else value
            for key, value in fields.items()}


def _same(key, actual, expected):
    if key in ("nac_checked_at", "nac_last_attempt"):
        try:
            return datetime.fromisoformat(str(actual).replace("Z", "+00:00")) == datetime.fromisoformat(expected.replace("Z", "+00:00"))
        except ValueError:
            return False
    if key == "nac_status":
        actual = _value(actual)
    return actual == expected


def sync_device(client, host, record, *, applying=False, poller=None):
    """Patch only NAC fields, and verify them; retain per-interface failures."""
    result = {"status": "written", "device_id": None, "interfaces": [], "errors": []}
    record["netbox_writeback"] = result
    try:
        result["device_id"] = device_id(client, host)
        rows = client.get("dcim/interfaces/", {"device_id": result["device_id"]})
        names = [row["name"] for row in rows]
        if len(names) != len(set(names)) or any(
                (row.get("device") or {}).get("id") != result["device_id"] for row in rows):
            raise NetBoxError("NetBox interface response has duplicate names or a mismatched device")
        audit, reason = snapshot(record, applying)
        if audit is not None and audit.get("status") == "unknown":
            reason = audit.get("reason")
        ports = {port["name"]: port for port in (audit or {}).get("interfaces", [])}
        missing = sorted(set(ports) - set(names))
        if missing:
            result["errors"].append("Interfaces missing in NetBox: " + ", ".join(missing))
        if not rows:
            result["errors"].append("No interfaces exist on this device in NetBox; run SNMP inventory first.")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for row in rows:
            fields = fields_for(ports.get(row["name"]), now, poller or socket.gethostname(),
                                reason=reason, checked_at=(audit or {}).get("checked_at"))
            item = {"id": row["id"], "name": row["name"], "custom_fields": fields, "status": "pending"}
            result["interfaces"].append(item)
            path = f"dcim/interfaces/{int(row['id'])}/"
            try:
                if all(_same(key, (row.get("custom_fields") or {}).get(key), value) for key, value in fields.items()):
                    item["status"] = "unchanged"
                    continue
                client.request_object("PATCH", path, {"custom_fields": fields})
                observed = client.request_object("GET", path)
                if not all(_same(key, (observed.get("custom_fields") or {}).get(key), value) for key, value in fields.items()):
                    raise NetBoxError(f"interface {row['name']}: NAC fields did not verify after write")
                item["status"] = "written"
            except NetBoxError as exc:
                item.update(status="failed", error=redact(str(exc)))
                result["errors"].append(item["error"])
    except (NetBoxError, ValueError, KeyError) as exc:
        result["errors"].append(redact(str(exc)))
    if result["errors"]:
        result["status"] = "failed"
    return result
