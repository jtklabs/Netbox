"""Existing F5 WAF remote destinations, with NetBox policy and audit dates."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from datetime import datetime, timezone

from .. import f5_waf
from ..core import Desired, Feature, MODE_REPLACE, canonical_platform
from ..standards import host_and_port, of

POLICY_TAGS = {"syslog-audit": "audit", "syslog-add": "add", "syslog-manage": "replace"}
CHECKED_FIELD = "syslog_last_checked"


def add_arguments(parser):
    group = parser.add_argument_group("F5 HTTPS")
    group.add_argument("--f5-port", type=int, default=os.environ.get("NETOPS_F5_PORT", "443"),
                       help="BIG-IP management HTTPS port [$NETOPS_F5_PORT]")
    group.add_argument("--f5-timeout", type=float, default=os.environ.get("NETOPS_F5_TIMEOUT", "30"),
                       help="REST request timeout in seconds [$NETOPS_F5_TIMEOUT]")
    tls = group.add_mutually_exclusive_group()
    tls.add_argument("--f5-insecure", action="store_true", default=None,
                     help="disable BIG-IP certificate verification [$NETOPS_F5_VERIFY_TLS=false]")
    tls.add_argument("--f5-verify-tls", dest="f5_insecure", action="store_false", default=None,
                     help="enable BIG-IP certificate verification, overriding $NETOPS_F5_VERIFY_TLS")
    group.add_argument("--f5-login-provider", default=os.environ.get("NETOPS_F5_LOGIN_PROVIDER", "tmos"),
                       help="BIG-IP authentication provider [$NETOPS_F5_LOGIN_PROVIDER]")
    group.add_argument("--netbox-checked-field", default=os.environ.get("NETBOX_CHECKED_FIELD", CHECKED_FIELD),
                       help="device datetime custom field for the last syslog check [$NETBOX_CHECKED_FIELD]")


def connection_settings(args):
    if args.f5_insecure is None:
        tls = os.environ.get("NETOPS_F5_VERIFY_TLS", "true").strip().lower()
        if tls not in ("true", "false"):
            raise ValueError("NETOPS_F5_VERIFY_TLS must be true or false")
        verify_tls = tls == "true"
    else:
        verify_tls = not args.f5_insecure
    if not 1 <= args.f5_port <= 65535 or args.f5_timeout <= 0:
        raise ValueError("F5 HTTPS port must be 1–65535 and timeout must be positive")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", args.netbox_checked_field):
        raise ValueError("the NetBox checked field must be an identifier, e.g. syslog_last_checked")
    return {"port": args.f5_port, "verify_tls": verify_tls, "timeout": args.f5_timeout,
            "provider": args.f5_login_provider}


def build_desired(args):
    connection = connection_settings(args)
    destinations = []
    for raw in of(args).entries("syslog.destinations"):
        item = host_and_port(raw, 514)
        try:
            host = str(ipaddress.ip_address(item["host"]))
        except ValueError as exc:
            raise ValueError(f"WAF requires literal IPs in syslog.destinations: {item['host']!r}") from exc
        port = item["port"]
        if isinstance(port, bool) or not re.fullmatch(r"\d+", str(port)) or not 1 <= int(port) <= 65535:
            raise ValueError(f"invalid syslog destination port: {port!r}")
        destination = (host, int(port))
        if destination not in destinations:
            destinations.append(destination)
    if not destinations:
        raise ValueError("define syslog.destinations in the standards file before managing WAF")
    return Desired(
        keys=[f"{host}{'.' if ':' in host else ':'}{port}" for host, port in destinations],
        variables={"destinations": destinations, **connection,
                   "netbox_policy": bool(getattr(args, "netbox", False))},
    )


def device_policy(host, mode, netbox=False):
    if not netbox:
        return mode, False, "CLI policy"
    tags = {tag.strip() for tag in str(host.data.get("tags", "")).split(",")}
    matches = sorted(tags & POLICY_TAGS.keys())
    if len(matches) > 1:
        raise ValueError(f"conflicting NetBox syslog policy tags: {', '.join(matches)}")
    policy = POLICY_TAGS[matches[0]] if matches else "audit"
    return (MODE_REPLACE if policy == "audit" else policy,
            policy == "audit", matches[0] if matches else "untagged: audit")


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result

    host = task.host
    platform = canonical_platform(host.platform)
    payload = {
        "platform": platform, "mode": mode, "current": [], "desired": list(desired),
        "add": [], "remove": [], "commands": [], "save_command": None,
        "compliant": False, "advisories": [], "notes": [], "rollback": [],
        "rollback_unsupported": [], "applied": False, "saved": None,
        "skipped": False, "skip_reason": None, "verified": None,
        "missing_after": [], "output": None, "save_output": None,
        "profiles": [], "skipped_profiles": [], "checked_at": None, "syslog_compliant": None,
    }
    if not platform:
        raise ValueError("WAF needs an explicit F5 platform in NetBox/CSV or --platform f5_tmsh")
    if platform != "f5_tmsh":
        payload.update(skipped=True, skip_reason="WAF logging applies only to F5 BIG-IP")
        return Result(host=host, result=payload)
    mode, audit_only, policy = device_policy(host, mode, variables["netbox_policy"])
    clean = mode == MODE_REPLACE
    payload.update(mode="audit" if audit_only else mode, audit_only=audit_only, policy=policy)
    payload["notes"].append(f"policy: {policy}")
    if audit_only:
        payload["notes"].append("audit only: F5 settings are read and compared; no configuration is written")
    if not host.username or not host.password:
        raise ValueError("F5 REST requires a username and password; SSH keys cannot authenticate it")

    attempted = False
    errors = []
    try:
        from .. import f5_syslog

        with f5_waf.Client(host, **{k: variables[k] for k in
                                   ("port", "verify_tls", "timeout", "provider")}) as client:
            plans = f5_waf.plan_waf(client, variables["destinations"], clean,
                                    skipped_profiles=payload["skipped_profiles"])
            system_plan = f5_syslog.plan_syslog(client.get_json(f5_syslog.SYSLOG),
                                              variables["destinations"], True)
            system_exact = not system_plan.drift(True)
            payload["system_syslog_audit"] = {
                "compliant": system_exact, "current": system_plan.current,
                "missing": system_plan.add, "extra": system_plan.extra,
            }
            payload["notes"].append(
                "system syslog audited for combined compliance; use configure.py syslog to change it")
            payload["notes"].extend(
                f"skipped {row['profile']}: {row['reason']}"
                for row in payload["skipped_profiles"])
            ownership_unknown = any(row["built_in"] is None
                                    for row in payload["skipped_profiles"])
            if ownership_unknown:
                payload["notes"].append(
                    "syslog_compliant left unchanged: some profile ownership could not be confirmed")
            if not plans:
                payload.update(skipped=True, skip_reason="no confirmed user-defined WAF profiles with existing remote servers")
            for plan in plans:
                row = {"profile": plan.label, "endpoint": plan.endpoint,
                       "current": plan.current, "keep": plan.keep, "add": plan.add,
                       "extra": plan.extra, "remove": plan.extra if clean else [],
                       "payload": plan.payload, "applied": False, "verified": None,
                       "fully_managed_compliant": not plan.drift(True)}
                # Keep exact original server objects for a manual REST reversal.
                row["before_servers"] = plan.before_servers
                payload["profiles"].append(row)
                payload["current"].extend(f"{plan.label}: {entry}" for entry in plan.current)
                payload["add"].extend(f"{plan.label}: {entry}" for entry in plan.add)
                payload["remove"].extend(f"{plan.label}: {entry}" for entry in row["remove"])
                payload["notes"].append(
                    f"{plan.label}: {len(plan.keep)} standard, {len(plan.add)} missing, "
                    f"{len(plan.extra)} extra" + (" (preserved)" if not clean else "")
                )
                if not plan.drift(clean):
                    row["verified"] = True
                    continue
                payload["commands"].append(f"PATCH {plan.endpoint} {json.dumps(plan.payload)}")
                if save and not audit_only:
                    payload["save_command"] = 'POST /mgmt/tm/sys/config {"command": "save"}'
                if dry_run or audit_only:
                    continue
                try:
                    attempted = True
                    client.patch_json(plan.endpoint, plan.payload)
                    payload["applied"] = row["applied"] = True
                    row["fully_managed_compliant"] = None
                    if verify:
                        after = client.get_json(plan.endpoint)
                        again = f5_waf.plan_waf_application(
                            after, variables["destinations"], clean, plan.label, plan.endpoint)
                        missing = again.add + (again.extra if clean else [])
                        if after.get("remoteStorage", "none") == "none":
                            missing.append("remote logging is disabled")
                        row["verified"] = not missing
                        row["fully_managed_compliant"] = (
                            not again.drift(True) and after.get("remoteStorage", "none") != "none")
                        row["after"] = again.current
                        payload["missing_after"].extend(f"{plan.label}: {item}" for item in missing)
                except Exception as exc:
                    row["error"] = str(exc)
                    errors.append(f"{plan.label}: {exc}")
            payload["compliant"] = bool(plans) and not payload["commands"] and not ownership_unknown
            if attempted and verify:
                payload["verified"] = not errors and not payload["missing_after"]
            if payload["applied"] and save:
                if not errors and payload["verified"] is not False:
                    client.save_config()
                    payload["saved"] = True
                else:
                    payload["saved"] = False
            if errors:
                raise RuntimeError("; ".join(errors))
            if payload["verified"] is not False and (not attempted or verify):
                payload["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                if not ownership_unknown:
                    # Inventory policy controls writes, not the compliance target:
                    # add-only with extra destinations is not fully managed.
                    payload["syslog_compliant"] = system_exact and all(
                        row["fully_managed_compliant"] is True for row in payload["profiles"])
            if attempted:
                payload["rollback_unsupported"] = [
                    "REST rollback is manual; profiles.before_servers in --report records the original lists"]
                payload["notes"].extend(payload["rollback_unsupported"])
        return Result(host=host, result=payload, changed=attempted)
    except Exception as exc:
        payload["checked_at"] = None
        payload["syslog_compliant"] = None
        return Result(host=host, result=payload, changed=attempted, failed=True, exception=exc)


def selftest(desired):
    for clean in (False, True):
        plan = f5_waf.plan_waf_application(
            {"servers": [{"name": "192.0.2.99:514"}]},
            desired.variables["destinations"], clean, "example", "/example/application/remote")
        print(f"  {'replace' if clean else 'add'}: PATCH {plan.endpoint} {json.dumps(plan.payload)}")
    return 0


FEATURE = Feature(
    name="waf", help="audit or manage existing F5 WAF remote logging destinations",
    platforms={}, add_arguments=add_arguments, build_desired=build_desired,
    run=run, selftest=selftest, reversible=False,
)
