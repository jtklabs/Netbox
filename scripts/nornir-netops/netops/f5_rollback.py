"""Validated BIG-IP REST reversals from run archives and rollback journals."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from urllib.parse import unquote

from . import archive, debuglog, f5_snmp, f5_waf
from .rollback import RollbackError

SYSLOG = "/mgmt/tm/sys/syslog"
SSH = "/mgmt/tm/sys/sshd"
GUI = "/mgmt/tm/sys/global-settings"
SECRET_FIELDS = {"authPassword", "privacyPassword", "communityName"}
MARKERS = ("<redacted>", "<hidden>", "<previous-secret-required>")
USER_FIELDS = {"name", "partition", "fullPath", "username", "access", "oidSubset", "securityLevel",
               "authProtocol", "privacyProtocol", "description", "authPassword", "privacyPassword"}
COMMUNITY_FIELDS = {"name", "partition", "fullPath", "access", "ipv6", "oidSubset", "source", "description", "communityName"}
ROOT_FIELDS = {"allowedAddresses", "snmpv1", "snmpv2c", "sysContact", "sysLocation"}


def command(step):
    return step["method"] + " " + step["path"] + (
        " " + json.dumps(archive.clean(step["body"])) if step.get("body") is not None else "")


def path_check(path):
    if not isinstance(path, str) or not path.startswith("/mgmt/tm/"):
        raise RollbackError("F5 rollback requires a local /mgmt/tm/ REST path")
    decoded = unquote(path)
    if any(c in decoded for c in ("?", "#", "\\", "%", "\r", "\n")) or "//" in decoded or any(
            part in (".", "..") for part in decoded.replace("~", "/").split("/")[1:]):
        raise RollbackError("invalid F5 rollback REST path")
    return path


def collection_for(path):
    return next((base for base in (f5_snmp.USERS, f5_snmp.COMMUNITIES)
                 if path == base or path.startswith(base + "/")), None)


def journal_record(feature, row):
    """Retain the archive's original evidence and restore operations only."""
    backout = row.get("backout", {})
    return {"hostname": row.get("hostname"), "platform": "f5_tmsh", "transport": "rest",
            "feature": feature, "restore_steps": [copy.deepcopy(s) for s in backout.get("steps", [])
                                                   if s.get("purpose") == "restore"],
            "original_config": row.get("current_config", {}).get("config"),
            "unsupported": backout.get("limitations", []), "applied": row.get("commands", [])}


def steps_for(record):
    """Restrict replay to the fields and endpoints managed by the feature."""
    feature = record.get("feature")
    if feature not in ("waf", "syslog", "banner", "snmp"):
        raise RollbackError("unsupported F5 rollback feature")
    raw = record.get("restore_steps")
    if not isinstance(raw, list) or not raw:
        raise RollbackError("F5 rollback contains no REST restore steps")
    steps, targets = [], set()
    for item in raw:
        if not isinstance(item, dict) or item.get("transport") != "rest":
            raise RollbackError("F5 rollback contains a non-REST step")
        step = copy.deepcopy(item)
        path = path_check(step.get("path"))
        method, body = step.get("method"), step.get("body")
        base = collection_for(path) if feature == "snmp" else None
        fields = None
        if feature == "syslog" and path == SYSLOG:
            fields = {"remoteServers"}
        elif feature == "banner":
            fields = {SSH: {"banner", "bannerText"}, GUI: {"guiSecurityBanner", "guiSecurityBannerText"}}.get(path)
        elif feature == "waf":
            suffix = path.removeprefix(f5_waf.WAF_PROFILES + "/").split("/")
            if path.startswith(f5_waf.WAF_PROFILES + "/") and len(suffix) == 3 and suffix[1] == "application":
                fields = {"servers"}
        elif feature == "snmp":
            fields = ROOT_FIELDS if path == f5_snmp.SNMP else USER_FIELDS if base == f5_snmp.USERS else COMMUNITY_FIELDS if base else None
        if fields is None:
            raise RollbackError("F5 rollback endpoint is outside the selected feature")
        if base:
            if path != base and "/" in path[len(base) + 1:]:
                raise RollbackError("invalid F5 SNMP resource path")
            if method not in ("POST", "PATCH", "DELETE") or (method == "POST") != (path == base):
                raise RollbackError("invalid F5 SNMP rollback method/resource")
        elif method != "PATCH":
            raise RollbackError("F5 rollback scalar settings require PATCH")
        if method == "DELETE":
            if body is not None:
                raise RollbackError("F5 rollback DELETE must not contain a body")
        elif not isinstance(body, dict) or not body or set(body) - fields:
            raise RollbackError("F5 rollback contains unsupported or missing fields")
        if base and method == "POST":
            target = step.get("target")
            if not target:
                # Earlier archives recorded resource paths only in the snapshot.
                section = "users" if base == f5_snmp.USERS else "communities"
                rows = (record.get("original_config") or {}).get(section, [])
                matches = [r["path"] for r in rows if r.get("name") == body.get("name")]
                if len(matches) != 1:
                    raise RollbackError("original SNMP resource identity is missing or ambiguous")
                target = matches[0]
            target = path_check(target)
            if not target.startswith(base + "/") or "/" in target[len(base) + 1:]:
                raise RollbackError("invalid original SNMP resource identity")
        else:
            target = path
        if target in targets:
            raise RollbackError("duplicate F5 rollback target")
        targets.add(target)
        step.update(target=target, purpose="restore")
        steps.append(step)
    return steps


def load_secrets(args):
    filename, secret = args.rollback_secrets, args.rollback_secret
    if not (filename or secret):
        filename, secret = os.environ.get("NETOPS_ROLLBACK_SECRETS_FILE"), os.environ.get("NETOPS_ROLLBACK_SECRET")
    if filename and secret:
        raise RollbackError("choose --rollback-secrets or --rollback-secret")
    if secret:
        from .credentials import fetch_json_secret
        document = fetch_json_secret(secret, args.aws_region)
    elif filename:
        try:
            document = json.loads(Path(filename).expanduser().read_text())
        except (OSError, ValueError) as exc:
            raise RollbackError("could not read rollback secrets JSON") from exc
    else:
        return {}
    if not isinstance(document, dict):
        raise RollbackError("rollback secrets must map device names to resource paths and secret fields")
    values = []
    for resources in document.values():
        if not isinstance(resources, dict):
            raise RollbackError("invalid rollback secrets device mapping")
        for path, fields in resources.items():
            path_check(path)
            if not isinstance(fields, dict) or set(fields) - SECRET_FIELDS:
                raise RollbackError("rollback secrets accept only authPassword, privacyPassword and communityName")
            if any(not isinstance(v, str) or not v or any(m in v for m in MARKERS) for v in fields.values()):
                raise RollbackError("rollback secret values must be nonempty strings without placeholders")
            values.extend(fields.values())
    debuglog.protect(values + [json.dumps(value)[1:-1] for value in values])
    return document


def prepare(steps, supplied):
    """Fill only marked secret fields; never use a secret file to alter settings."""
    steps = copy.deepcopy(steps)
    blocked = []
    for step in steps:
        body = step.get("body")
        if body is None:
            continue
        base = collection_for(step["target"])
        if base == f5_snmp.USERS:
            required = ("name", "username", "access", "oidSubset", "securityLevel", "authProtocol", "privacyProtocol")
            if any(body.get(k) is None for k in required):
                blocked.append(f"{step['target']}: original user settings are incomplete")
        if base == f5_snmp.COMMUNITIES and any(body.get(k) is None for k in ("name", "access", "oidSubset", "source")):
            blocked.append(f"{step['target']}: original community settings are incomplete")
        needed = []
        if base == f5_snmp.COMMUNITIES:
            needed.append("communityName")
        elif base == f5_snmp.USERS:
            if body.get("authProtocol") not in (None, "none"):
                needed.append("authPassword")
            if body.get("privacyProtocol") not in (None, "none"):
                needed.append("privacyPassword")
        for key in needed:
            body.setdefault(key, "<previous-secret-required>")
        for key, value in list(body.items()):
            if key in SECRET_FIELDS and isinstance(value, str) and any(m in value for m in MARKERS):
                replacement = supplied.get(step["target"], {}).get(key)
                if replacement:
                    body[key] = replacement
                else:
                    blocked.append(f"{step['target']}: supply previous {key}")
            elif value is None or any(m in str(value) for m in MARKERS):
                blocked.append(f"{step['target']}: original {key} is unavailable")
    return steps, blocked


def read(client, steps):
    collections = {}
    observations = {}
    for step in steps:
        path = step["target"]
        base = collection_for(path)
        if base:
            if base not in collections:
                rows = f5_snmp.collection(client, base, [])
                collections[base] = {f5_snmp.resource(row, base): row for row in rows}
                if len(collections[base]) != len(rows):
                    raise RollbackError("BIG-IP returned duplicate SNMP resource identities")
            raw = collections[base].get(path)
            if raw is not None:
                allowed = USER_FIELDS if base == f5_snmp.USERS else COMMUNITY_FIELDS
                raw = {k: v for k, v in raw.items() if k in allowed and k not in SECRET_FIELDS}
                if base == f5_snmp.USERS:
                    raw.update(f5_snmp.metadata(raw))
        else:
            raw = client.get_json(path)
            fields = step["body"]
            raw = {key: raw.get(key, [] if key in ("servers", "remoteServers") else None) for key in fields}
        observations[path] = raw
    return observations


def matches(step, actual):
    if step["method"] == "DELETE":
        return actual is None
    if actual is None:
        return False
    return all(actual.get(key) == value for key, value in step["body"].items()
               if key not in SECRET_FIELDS and key not in ("name", "partition", "fullPath"))


def operation(step, actual):
    item = copy.deepcopy(step)
    path = step["target"]
    base = collection_for(path)
    if base and step["method"] != "DELETE":
        item["method"] = "POST" if actual is None else "PATCH"
        item["path"] = base if actual is None else path
        # Preserve partition/folder identity even in archives written before it
        # was included as a separate metadata field.
        if actual is None:
            item["body"]["name"] = unquote(path[len(base) + 1:]).replace("~", "/")
            item["body"].pop("fullPath", None)
            item["body"].pop("partition", None)
        else:
            for key in ("name", "partition", "fullPath"):
                item["body"].pop(key, None)
    return item


def inverse(steps, observations):
    result, limitations = [], []
    for step in reversed(steps):
        path = step["target"]
        before = observations[path]
        base = collection_for(path)
        required = []
        if before is None:
            result.append({"transport": "rest", "method": "DELETE", "path": path, "body": None, "purpose": "restore"})
            continue
        body = copy.deepcopy(before)
        if base:
            if base == f5_snmp.COMMUNITIES:
                required = ["communityName"]
            else:
                if body.get("authProtocol") not in (None, "none"):
                    required.append("authPassword")
                if body.get("privacyProtocol") not in (None, "none"):
                    required.append("privacyPassword")
            for key in required:
                body[key] = "<previous-secret-required>"
        if required:
            limitations.append(f"Supply previous {', '.join(required)} for {path} before restoring.")
        if any(value is None for value in body.values()):
            limitations.append(f"{path}: some previous settings were not returned by BIG-IP")
        result.append({"transport": "rest", "method": "POST" if step["method"] == "DELETE" else "PATCH",
                       "path": base if step["method"] == "DELETE" else path, "target": path,
                       "body": body, "requires_secret_fields": required, "purpose": "restore"})
    return result, limitations


def run(task, save, dry_run, settings, supplied, sink):
    from nornir.core.task import Result
    payload = {"platform": "f5_tmsh", "commands": [], "applied": False, "saved": None,
               "verified": None, "config_before": None, "config_after": None}
    attempted = False
    try:
        record = task.host.data
        steps = steps_for(record)
        steps, blocked = prepare(steps, supplied.get(task.host.name, {}))
        # Legacy archives express missing scalar/user metadata in these notes;
        # a credentials file can resolve only the explicitly secret-only notes.
        blocked.extend(reason for reason in record.get("unsupported", [])
                       if not reason.startswith("Supply previous ") and not reason.startswith(
                           "Restoring previous credentials requires secrets"))
        if not task.host.username or not task.host.password:
            raise RollbackError("F5 rollback requires a username and password")
        debuglog.protect([task.host.password])
        with f5_waf.Client(task.host, **settings) as client:
            if record["feature"] == "waf":
                eligible = {p.endpoint for p in f5_waf.plan_waf(client, [], False)}
                if any(step["target"] not in eligible for step in steps):
                    raise RollbackError("WAF rollback target is not a confirmed user-created profile with remote logging")
            before = read(client, steps)
            payload["config_before"] = [{"path": p, "config": body} for p, body in before.items()]
            planned = [operation(s, before[s["target"]]) for s in steps
                       if not matches(s, before[s["target"]]) or
                       any(k in SECRET_FIELDS for k in (s.get("body") or {}))]
            payload["commands"] = [command(s) for s in planned]
            payload["read_steps"] = [{"transport": "rest", "method": "GET", "path": collection_for(p) or p,
                                      "purpose": "read_config"} for p in before]
            payload["rollback_steps"], payload["rollback_unsupported"] = inverse(planned, before)
            payload["save_command"] = 'POST /mgmt/tm/sys/config {"command": "save"}' if save and planned else None
            payload["blocked"] = blocked
            if blocked:
                raise RollbackError("F5 rollback is incomplete: " + "; ".join(blocked))
            if dry_run or not planned:
                payload["verified"] = True if not planned else None
                return Result(host=task.host, result=payload)
            if sink:
                sink.record(task.host, payload, changed=True, status="applying")
            for item in planned:
                attempted = True
                client.request(item["method"], item["path"], item.get("body"))
            payload["applied"] = True
            after = read(client, steps)
            payload["config_after"] = [{"path": p, "config": body} for p, body in after.items()]
            payload["verified"] = all(matches(s, after[s["target"]]) for s in steps)
            if any(k in SECRET_FIELDS for s in steps for k in (s.get("body") or {})):
                payload["verification_note"] = "Readable settings verified; previous credential values cannot be read back or tested by the REST API."
            if not payload["verified"]:
                payload["saved"] = False if save else None
                raise RollbackError("F5 rollback readback does not match the archived configuration")
            if save:
                client.save_config()
                payload["saved"] = True
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        return Result(host=task.host, result=archive.clean(payload), failed=True, changed=attempted,
                      exception=RollbackError(debuglog.redact(str(exc))))
