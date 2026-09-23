"""BIG-IP upgrade driver: the IOS XE stages, gates and reporting over iControl REST.

Order of operations under --apply: save the configuration, baseline, verify or
upload the image, the ready gate, UCS backup, an optional failover to standby,
install to a boot volume, reboot to it, reconnect, then the shared convergence
loop and comparison report.
"""

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path

from nornir.core.task import Result

from .. import f5_waf
from . import checks, f5
from .images import local_image
from .profile import f5_version
from .workflow import comparison_report, converge, device_lock, settle

FAILOVER_WAIT = 120


def choose_volume(volumes, profile, active_name):
    names = [v.get("name") for v in volumes if v.get("name")]
    if profile.volume:
        if profile.volume == active_name:
            raise ValueError(f"profile volume {profile.volume} is the active boot location")
        return {"name": profile.volume, "create": profile.volume not in names}
    inactive = [v for v in volumes if v.get("name") and v.get("name") != active_name]
    preferred = next((v for v in inactive if v.get("version") and f5_version(v["version"]) == f5_version(profile.target_version)), None)
    chosen = preferred or (inactive[0] if inactive else None)
    if chosen:
        return {"name": chosen["name"], "create": False, "replaces_version": chosen.get("version")}
    numbers = [int(m[1]) for m in (re.match(r"HD1\.(\d+)$", n) for n in names) if m]
    return {"name": f"HD1.{max(numbers, default=1) + 1}", "create": True}


def preflight(snapshot, profile, stage_only=False, saved=False, allow_mismatch=False):
    blockers = [f"{key}: {value}" for key, value in snapshot["errors"].items()]
    info = snapshot.get("facts", {})
    unit = snapshot.get("software", {}).get("1", {})
    current = unit.get("version")
    if unit and unit.get("model") not in profile.models and info.get("platform_id") not in profile.models:
        blockers.append("hardware platform is not approved by this profile")
    at_target = bool(current) and f5_version(current) == f5_version(profile.target_version)
    if current and profile.starting_versions and not at_target and f5_version(current) not in {f5_version(v) for v in profile.starting_versions}:
        blockers.append("current release is not an approved starting version")
    plan = {"blockers": blockers, "bundle_conversion": False, "upgrade_needed": not at_target, "already_current": at_target,
            "starting_versions": [current] if current else [], "target_version": profile.target_version,
            "profile": profile.name, "approved_profile": asdict(profile), "operation": "stage_image" if stage_only else "upgrade",
            "unsaved_changes": False, "config_mismatch_overridden": False, "configuration_saved": saved,
            "active_volume": info.get("active_volume"), "failover_state": info.get("failover_state"), "peers": info.get("peers", [])}
    if stage_only:
        return plan
    if any(str(v.get("status", "")).lower() != "complete" for v in info.get("volumes", [])):
        blockers.append("a software installation is already in progress on a boot volume")
    sync = info.get("sync_status")
    if info.get("peers") and sync not in (None, "In Sync", "Standalone"):
        blockers.append(f"configuration sync is '{sync}'; sync the device group before upgrading")
    plan["failover_first"] = bool(info.get("failover_state") == "active" and info.get("peers"))
    if plan["failover_first"] and not profile.allow_active:
        blockers.append("unit is the active member of a device group; upgrade the standby first or set allow_active to fail over first")
    if profile.license_check_date:
        seen = info.get("license_service_check_date")
        if not seen:
            blockers.append("license service check date could not be read")
        elif seen < profile.license_check_date:
            blockers.append(f"license service check date {seen} is older than the target release's {profile.license_check_date}; reactivate the license first")
    plan["expected_failover_state"] = "standby" if info.get("peers") else "active"
    try:
        plan["volume"] = choose_volume(info.get("volumes", []), profile, info.get("active_volume"))
    except ValueError as exc:
        blockers.append(str(exc))
    return plan


def planned_commands(profile, plan, host):
    install = {"command": "install", "name": profile.image, "volume": plan["volume"]["name"]}
    if plan["volume"]["create"]:
        install["options"] = [{"create-volume": True}]
    commands = ['POST /mgmt/tm/sys/config {"command": "save"}']
    if profile.ucs_backup:
        commands.append(f'POST /mgmt/tm/sys/ucs {json.dumps({"command": "save", "name": ucs_name(host, profile)})}')
    if plan.get("failover_first"):
        commands.append('POST /mgmt/tm/sys/failover {"command": "run", "standby": true}')
    commands.append(f"POST /mgmt/tm/sys/software/image {json.dumps(install)}")
    commands.append(f'POST /mgmt/tm/sys {json.dumps({"command": "reboot", "options": [{"volume": plan["volume"]["name"]}]})}')
    return commands


def ucs_name(host, profile):
    return f"{re.sub(r'[^A-Za-z0-9._-]+', '-', host.name)[:60]}-pre-{profile.target_version}.ucs"


def verify_image(client, profile, options, plan, apply, emit):
    """Read-only unless apply: confirm the ISO is on the unit, or stage it."""
    free = f5.free_space_kb(client)
    plan["flash"] = {"1": {"free_bytes": free * 1024 if free is not None else None}}
    entry = f5.find_image(client, profile.image)
    if entry:
        digest = f5.remote_md5(client, profile.image)
        if digest == profile.md5.lower() or (digest is None and str(entry.get("verified", "")).lower() == "yes"):
            plan["image_verification"] = "verified"
            plan["image_size_bytes"] = entry.get("fileSize")
            return
        raise ValueError("an image with this name is already in /shared/images but its checksum differs; remove it before staging")
    if not profile.image_source:
        raise ValueError("target image is not in /shared/images and image_source is not configured")
    local = None
    if apply or not re.match(r"^https?://", profile.image_source):
        local = local_image(profile, options, emit)
    size = os.path.getsize(local) if local else None
    if free is not None and size is not None and free * 1024 < profile.minimum_free_bytes + size:
        raise ValueError("insufficient free space in /shared/images for the image and the configured reserve")
    plan["image_verification"] = "pending_transfer"
    plan["staging_command"] = f"POST /mgmt/cm/autodeploy/software-image-uploads/{profile.image}"
    if not apply:
        return
    emit("staging", f"Uploading {profile.image} to /shared/images")
    f5.upload_image(client, local, progress=lambda percent: emit("staging", f"Upload {percent}% complete"))
    emit("image_verification", "Waiting for the unit to verify the image checksum")
    entry, note = f5.wait_verified(client, profile.image, profile.md5, options.install_timeout)
    plan["image_verification"] = "verified"
    plan["image_verification_note"] = note
    plan["image_size_bytes"] = entry.get("fileSize")


def backup(client, host, profile, options, plan, emit):
    name = ucs_name(host, profile)
    passphrase = os.environ.get("NETOPS_F5_UCS_PASSPHRASE", "")
    if passphrase:
        from ..debuglog import protect
        protect([passphrase])
    directory = getattr(options, "ucs_dir", None)
    emit("backing_up", f"Saving UCS {name} on the unit" + (" and downloading it" if directory else ""))
    f5.save_ucs(client, name, passphrase)
    record = {"name": name, "on_unit": True, "encrypted": bool(passphrase)}
    if directory:
        destination = Path(directory) / name
        record["bytes"] = f5.download_ucs(client, name, destination)
        digest = f5.remote_md5(client, name, f5.UCS_DIR)
        if digest and digest != f5.local_md5(destination):
            raise ValueError("UCS download checksum does not match the archive on the unit")
        record.update(local_path=str(destination), md5_verified=bool(digest))
    plan["ucs_backup"] = record


def wait_for_state(client, wanted, options, emit):
    deadline = time.monotonic() + FAILOVER_WAIT
    while True:
        state = f5.facts(client.get_json).get("failover_state")
        if state == wanted:
            return
        if time.monotonic() >= deadline:
            raise ValueError(f"unit did not become {wanted} within {FAILOVER_WAIT}s; failover state is {state}")
        emit("failing_over", f"Waiting to become {wanted}; currently {state}")
        time.sleep(options.poll_interval)


def wait_for_install(client, volume, options, emit):
    deadline, heartbeat = time.monotonic() + options.install_timeout, time.monotonic()
    while True:
        status = f5.volume_status(client, volume)
        if status.lower() == "complete":
            return
        if re.search(r"fail|error|abort", status, re.I):
            raise ValueError(f"installation to {volume} reported: {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"installation to {volume} did not complete within {int(options.install_timeout)}s; last status {status!r}")
        if time.monotonic() - heartbeat >= 30:
            emit("installing", f"Installing to {volume}: {status}")
            heartbeat = time.monotonic()
        time.sleep(options.poll_interval)


def wait_for_target(host, settings, profile, volume, options, emit):
    """Reconnect after the reboot; returns an open client once the target volume is active."""
    deadline, last = time.monotonic() + options.reload_timeout, "unit has not returned"
    while time.monotonic() < deadline:
        emit("reconnecting", f"Waiting for the unit to boot {volume} on {profile.target_version}")
        client = None
        try:
            client = f5_waf.Client(host, **settings).__enter__()
            info = f5.facts(client.get_json)
            if info.get("active_volume") == volume and info.get("version") and f5_version(info["version"]) == f5_version(profile.target_version):
                return client
            last = f"unit reachable but active volume is {info.get('active_volume')} on {info.get('version')}"
        except Exception as exc:
            last = type(exc).__name__
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except Exception:
                pass
        time.sleep(options.poll_interval)
    raise TimeoutError(f"reboot deadline exceeded: {last}; manual recovery required")


def target_findings(after, profile, plan):
    findings = []
    info = after.get("facts", {})
    unit = after.get("software", {}).get("1", {})
    if (not unit or unit.get("mode") != plan["volume"]["name"] or not unit.get("version")
            or f5_version(unit["version"]) != f5_version(profile.target_version)):
        findings.append({"check": "software", "severity": "error", "message": "unit is not running the target release from the planned volume"})
    if not info.get("license_service_check_date"):
        findings.append({"check": "license", "severity": "error", "message": "license is not readable after the reboot"})
    if info.get("peers") and info.get("sync_status") not in ("In Sync", "Standalone"):
        findings.append({"check": "sync", "severity": "warning", "message": f"configuration sync is '{info.get('sync_status')}'; sync the device group once both members are upgraded"})
    if plan.get("expected_failover_state") and info.get("failover_state") != plan["expected_failover_state"]:
        findings.append({"check": "failover", "severity": "warning", "message": f"failover state is {info.get('failover_state')}, expected {plan['expected_failover_state']}"})
    return findings


def close(client):
    try:
        client.__exit__(None, None, None)
    except Exception:
        pass


def upgrade_device(task, profile, options, reporter):
    changed, plan = False, {}
    host = task.host
    stage_only = getattr(options, "stage_only", False)
    allow_mismatch = getattr(options, "allow_config_mismatch", False)
    settings = dict(getattr(options, "f5", None) or {})

    def emit(stage, message, payload=None, attachment=None):
        return reporter.emit(host, stage, message, payload, changed=changed, attachment=attachment)

    try:
        with device_lock(options.lock_dir, f"{host.hostname}:{settings.get('port', 443)}"):
            if host.platform not in {None, "", "f5_tmsh"}:
                raise ValueError("a BIG-IP profile requires an f5_tmsh device")
            host.platform = "f5_tmsh"
            if not host.username or not host.password:
                raise ValueError("BIG-IP REST requires a username and password")
            emit("connecting", "Connecting to iControl REST for image staging checks" if stage_only
                 else "Connecting to iControl REST for pre-upgrade verification")
            client = f5_waf.Client(host, **settings).__enter__()
            volume = None
            try:
                f5.extend_token(client)
                configuration_saved = False
                if options.apply and not stage_only:
                    emit("saving_config", "Saving the running configuration (save sys config)")
                    client.save_config()
                    configuration_saved = True
                before = f5.collect(client, lambda path: emit("precheck", path))
                plan = preflight(before, profile, stage_only=stage_only, saved=configuration_saved, allow_mismatch=allow_mismatch)
                plan["commands"] = [] if stage_only or "volume" not in plan else planned_commands(profile, plan, host)
                emit("precheck_complete", "Image staging checks captured" if stage_only else "Baseline and upgrade plan captured",
                     {"pre": before, "upgrade_plan": plan,
                      "progress_summary": {"counts": before["metrics"]["counts"], "target_version": profile.target_version,
                                           "starting_versions": plan["starting_versions"], "bundle_conversion": False,
                                           "unsaved_changes": False, "failover_state": plan.get("failover_state")},
                      "rollback_unsupported": ["Staged image files are retained for inspection; there are no configuration changes to roll back." if stage_only
                                               else f"Recovery is rebooting to the previous boot volume ({plan.get('active_volume')}); configuration rollback is not an image rollback."]})
                if plan.get("failover_first") and not stage_only:
                    emit("precheck", "Unit is the active member of its device group; the profile allows failing over before install"
                         if profile.allow_active else "Unit is the active member of its device group")
                if plan["already_current"] and not plan["blockers"] and not stage_only:
                    emit("already_current", f"Already running {profile.target_version} from {plan['active_volume']}")
                    return Result(host=host, result=plan)
                try:
                    verify_image(client, profile, options, plan, apply=False, emit=emit)
                except Exception as exc:
                    plan["blockers"].append(str(exc))
                if stage_only and "staging_command" in plan:
                    plan["commands"] = [plan["staging_command"]]
                if plan["blockers"]:
                    emit("blocked", "; ".join(plan["blockers"]), {"upgrade_plan": plan})
                    return Result(host=host, result=plan, failed=True)
                if not options.apply:
                    message = (("Image verified in /shared/images" if plan["image_verification"] == "verified" else "Image missing; upload planned")
                               if stage_only else "Prechecks passed; upgrade planned")
                    emit("dry_run_complete", message, {"upgrade_plan": plan})
                    return Result(host=host, result=plan)
                if stage_only and plan["image_verification"] == "verified":
                    emit("staged", "Image already present and checksum verified; no upload needed", {"upgrade_plan": plan})
                    return Result(host=host, result=plan)
                if not emit("ready", "Staging checks passed; uploading image only" if stage_only else "Prechecks passed; starting approved upgrade", {"upgrade_plan": plan}):
                    raise ValueError("progress webhook unavailable before apply; no install or reboot performed")
                if plan["image_verification"] == "pending_transfer":
                    changed = True
                    verify_image(client, profile, options, plan, apply=True, emit=emit)
                if stage_only:
                    emit("staged", "Image uploaded to /shared/images and checksum verified", {"upgrade_plan": plan})
                    return Result(host=host, result=plan, changed=changed)
                if profile.ucs_backup:
                    backup(client, host, profile, options, plan, emit)
                if plan.get("failover_first"):
                    emit("failing_over", "Active member: failing over to the peer before installing")
                    f5.failover_to_standby(client)
                    wait_for_state(client, "standby", options, emit)
                    changed = True
                volume = plan["volume"]["name"]
                changed = True
                emit("installing", f"Installing {profile.image} to boot volume {volume}" + (" (new volume)" if plan["volume"]["create"] else ""))
                f5.install(client, profile.image, volume, plan["volume"]["create"])
                wait_for_install(client, volume, options, emit)
                emit("installing", f"Installation complete on {volume}; rebooting the unit to it")
                f5.reboot_to(client, volume)
            finally:
                close(client)
            client = wait_for_target(host, settings, profile, volume, options, emit)
            try:
                settle(emit, options)
                return converge(emit, options, before, plan, reporter, host,
                                lambda: f5.collect(client, lambda path: emit("postcheck", path)),
                                lambda after: target_findings(after, profile, plan))
            finally:
                close(client)
    except Exception as exc:
        from ..debuglog import redact
        message = redact(str(exc)) if isinstance(exc, (ValueError, TimeoutError, RuntimeError)) else type(exc).__name__
        status = ("staging_failed" if stage_only else "recovery_required") if changed else "failed"
        emit(status, message, {"error": message, "upgrade_plan": plan})
        return Result(host=host, result={"error": message}, failed=True, changed=changed)
