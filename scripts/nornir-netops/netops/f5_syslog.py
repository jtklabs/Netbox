"""BIG-IP system syslog remote servers, through the management REST API."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import datetime, timezone

from . import archive
from . import f5_waf
from .core import MODE_REPLACE

SYSLOG = "/mgmt/tm/sys/syslog"


def destination(host, port=514):
    address, sep, domain = str(host).partition("%")
    address = str(ipaddress.ip_address(address))
    if sep:
        if not domain.isdecimal():
            raise ValueError(f"invalid F5 route domain: {host!r}")
        if int(domain):
            address += "%" + str(int(domain))
    if isinstance(port, bool) or not str(port).isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError(f"invalid syslog port: {port!r}")
    return address, int(port)


def label(key):
    host, port = key
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def plan_syslog(document, wanted, clean):
    if not isinstance(document, dict) or "items" in document:
        raise ValueError("invalid F5 system syslog response")
    servers = document.get("remoteServers", [])
    if not isinstance(servers, list) or any(not isinstance(s, dict) for s in servers):
        raise ValueError("invalid F5 remoteServers list")
    plan = f5_waf.Plan(label="F5 system syslog", endpoint=SYSLOG, payload={},
                       before_servers=list(servers))
    matched, names, kept = set(), set(), []
    for server in servers:
        if not server.get("name"):
            raise ValueError("F5 syslog remote server has no name")
        names.add(str(server["name"]).rsplit("/", 1)[-1])
        key = destination(server.get("host"), server.get("remotePort", 514))
        plan.current.append(label(key))
        if key in wanted and key not in matched:
            matched.add(key)
            kept.append(dict(server))
            plan.keep.append(label(key))
        else:
            plan.extra.append(label(key))
    added = []
    for key in wanted:
        if key in matched:
            continue
        matched.add(key)
        plan.add.append(label(key))
        stem = "syslog_" + hashlib.sha256(label(key).encode()).hexdigest()[:12]
        name, suffix = stem, 1
        while name in names:
            name = f"{stem}_{suffix}"
            suffix += 1
        names.add(name)
        added.append({"name": name, "host": key[0], "remotePort": key[1]})
    plan.payload = {"remoteServers": (kept if clean else servers) + added}
    return plan


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result
    from .features.waf import device_policy

    host = task.host
    payload = {
        "platform": "f5_tmsh", "mode": mode, "current": [], "desired": [],
        "add": [], "remove": [], "commands": [], "save_command": None,
        "compliant": False, "advisories": [], "notes": [], "rollback": [],
        "rollback_unsupported": [], "applied": False, "saved": None,
        "skipped": False, "skip_reason": None, "verified": None,
        "missing_after": [], "output": None, "save_output": None,
        "checked_at": None, "syslog_compliant": None,
    }
    attempted = False
    try:
        mode, audit_only, policy = device_policy(host, mode, variables["netbox_policy"],
                                                variables.get("logging_policy"))
        payload.update(mode="audit" if audit_only else mode, audit_only=audit_only, policy=policy)
        payload["notes"].append(f"policy: {policy}")
        wanted = list(dict.fromkeys(destination(e["host"], e["port"])
                                   for e in variables["entries"].values() if e["kind"] == "host"))
        if not wanted:
            raise ValueError("F5 syslog requires collector IPs in syslog.destinations or --destination")
        payload["desired"] = [label(key) for key in wanted]
        if any(e["kind"] != "host" for e in variables["entries"].values()) or variables.get("vrf"):
            payload["notes"].append("F5 manages collector IPs and ports only; severity, source interface, origin-id and VRF settings do not apply")
        if not host.username or not host.password:
            raise ValueError("F5 REST requires a username and password")
        clean = mode == MODE_REPLACE
        with f5_waf.Client(host, **variables["f5"]) as client:
            plan = plan_syslog(client.get_json(SYSLOG), wanted, clean)
            skipped = []
            waf_plans = f5_waf.plan_waf(client, wanted, True, skipped_profiles=skipped)
            waf_exact = (None if any(row["built_in"] is None for row in skipped) else
                         all(not row.drift(True) for row in waf_plans))
            payload["waf_audit"] = {
                "compliant": waf_exact, "applicable_profiles": len(waf_plans),
                "skipped_profiles": skipped,
                "profiles": [{"profile": row.label, "missing": row.add, "extra": row.extra}
                             for row in waf_plans],
            }
            payload["notes"].append("WAF destinations audited for combined compliance; use configure.py waf to change them")
            payload.update(current=plan.current, add=plan.add,
                           remove=plan.extra if clean else [], before_servers=plan.before_servers)
            exact = not plan.drift(True)
            payload["compliant"] = not plan.drift(clean)
            if plan.drift(clean):
                payload["commands"] = [f"PATCH {SYSLOG} {json.dumps(plan.payload)}"]
                if save and not audit_only:
                    payload["save_command"] = 'POST /mgmt/tm/sys/config {"command": "save"}'
                if not dry_run and not audit_only:
                    attempted = True
                    payload["rollback_unsupported"] = ["REST rollback is manual; before_servers in --report records the original list"]
                    payload["notes"].extend(payload["rollback_unsupported"])
                    archive.checkpoint(task, payload)
                    client.patch_json(SYSLOG, plan.payload)
                    payload["applied"] = True
                    if verify:
                        after = plan_syslog(client.get_json(SYSLOG), wanted, clean)
                        payload["after"] = after.current
                        payload["config_after"] = {"remoteServers": after.before_servers}
                        payload["missing_after"] = after.add + (after.extra if clean else [])
                        payload["verified"] = not after.drift(clean)
                        exact = not after.drift(True)
                    if save:
                        if payload["verified"] is not False:
                            client.save_config()
                            payload["saved"] = True
                        else:
                            payload["saved"] = False
            if payload["verified"] is not False and (not attempted or verify):
                payload["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                payload["system_syslog_compliant"] = exact
                payload["syslog_compliant"] = exact and waf_exact if waf_exact is not None else None
        return Result(host=host, result=payload, changed=attempted)
    except Exception as exc:
        payload["checked_at"] = None
        payload["syslog_compliant"] = None
        return Result(host=host, result=payload, changed=attempted, failed=True, exception=exc)
