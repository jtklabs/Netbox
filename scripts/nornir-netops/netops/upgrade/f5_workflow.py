"""BIG-IP upgrade driver: the IOS XE stages, gates and reporting over iControl REST.

Order of operations under --apply: save the configuration, baseline, verify or
upload the image, the ready gate, UCS backup, install to a boot volume, reboot,
reconnect, convergence, then one new WAF request log. Standalone units can disable VIPs after install,
observe connections before reboot, and restore them after reconnect.
Without draining, an optional failover happens before installation as usual.
"""

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path

from nornir.core.task import Result

from .. import cancellation
from .. import f5_waf
from . import checks, f5, f5_drain, f5_requests, f5_validation
from .images import local_image
from .profile import F5_IMAGE, f5_version
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
            "active_volume": info.get("active_volume"), "failover_state": info.get("failover_state"), "peers": info.get("peers", []),
            "license_unavailable_before": info.get("license_unavailable", False)}
    if stage_only:
        return plan
    if profile.drain_connections and (info.get("peers") or info.get("failover_state") != "active"):
        blockers.append("connection draining is supported only on standalone BIG-IP units; "
                        "HA peers or a non-active/unknown failover state prevent this upgrade")
    if any(str(v.get("status", "")).lower() != "complete" for v in info.get("volumes", [])):
        blockers.append("a software installation is already in progress on a boot volume")
    sync = info.get("sync_status")
    if info.get("peers") and sync not in (None, "In Sync", "Standalone"):
        blockers.append(f"configuration sync is '{sync}'; sync the device group before upgrading")
    plan["failover_first"] = bool(info.get("failover_state") == "active" and info.get("peers"))
    if plan["failover_first"] and not profile.allow_active:
        blockers.append("unit is the active member of a device group; upgrade the standby first or set allow_active to fail over first")
    if not info.get("license_service_check_date") and not info.get("license_unavailable"):
        blockers.append("license response could not be interpreted; verify licensing before upgrading")
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
        commands.append(f'POST /mgmt/tm/sys/ucs {json.dumps({"command": "save", "name": plan.setdefault("ucs_name", ucs_name(host, profile))})}')
    if plan.get("failover_first"):
        commands.append('POST /mgmt/tm/sys/failover {"command": "run", "standby": true}')
    if profile.drain_connections:
        commands += ['PATCH /mgmt/tm/ltm/virtual/<originally-enabled-virtual> {"disabled": true}',
                     'GET /mgmt/tm/ltm/virtual/stats (wait for zero connections or a plateau)',
                     'POST /mgmt/tm/sys/config {"command": "save"} (persist disabled boot state)']
    commands.append(f"POST /mgmt/tm/sys/software/image {json.dumps(install)}")
    commands.append(f"POST /mgmt/tm/util/bash (cpcfg --source={plan['active_volume']} {plan['volume']['name']}; require exit 0)")
    commands.append(f'POST /mgmt/tm/sys {json.dumps({"command": "reboot", "volume": plan["volume"]["name"]})}')
    if profile.drain_connections:
        commands += ['PATCH /mgmt/tm/ltm/virtual/<originally-enabled-virtual> {"enabled": true} (after WAF and health validation)',
                     'POST /mgmt/tm/sys/config {"command": "save"} (persist restored states)']
    return commands


def ucs_name(host, profile):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{re.sub(r'[^A-Za-z0-9._-]+', '-', host.name)[:60]}-pre-{profile.target_version}-{stamp}-{uuid.uuid4().hex[:12]}.ucs"


def ensure_image_space(client, profile, plan, required, apply, emit):
    """Plan cleanup read-only; apply it only after the job's ready gate."""
    free = f5.free_space_kb(client)
    plan["flash"] = {"1": {"free_bytes": free * 1024 if free is not None else None}}
    if free is None and plan.get("image_cleanup"):
        raise ValueError("free space in /shared/images could not be verified for planned installer cleanup")
    if free is None or free * 1024 >= required:
        plan.pop("image_cleanup", None)
        return
    candidates = [row["name"] for row in f5.listed_images(client)
                  if row.get("name") and row["name"] != profile.image]
    if not candidates:
        raise ValueError("insufficient free space in /shared/images and no other uploaded installers can be removed")
    volumes = client.get_json("/mgmt/tm/sys/software/volume").get("items")
    if (not isinstance(volumes, list) or not volumes
            or any(str(v.get("status", "")).lower() != "complete" for v in volumes)):
        raise ValueError("installer cleanup requires readable boot volumes with no installation in progress")
    cleanup = plan["image_cleanup"] = {"candidates": candidates, "required_free_bytes": required,
                                       "free_bytes_before": free * 1024, "removed": [], "failed": []}
    if not apply:
        return
    emit("staging", "Removing previously uploaded installers to free space in /shared/images",
         {"upgrade_plan": plan})
    cleanup["removed"], cleanup["failed"] = f5.prune_images(client, profile.image, required)
    free = f5.free_space_kb(client)
    cleanup["free_bytes_after"] = free * 1024 if free is not None else None
    plan["flash"]["1"]["free_bytes"] = cleanup["free_bytes_after"]
    emit("staging", f"Installer cleanup removed {len(cleanup['removed'])} image(s); "
         f"{len(cleanup['failed'])} deletion(s) failed", {"upgrade_plan": plan})
    if free is None:
        raise ValueError("free space in /shared/images could not be verified after installer cleanup")
    if free * 1024 < required:
        raise ValueError("insufficient free space in /shared/images after installer cleanup for the image and the configured reserve")


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
            ensure_image_space(client, profile, plan, profile.minimum_free_bytes, apply, emit)
            return
        raise ValueError("an image with this name is already in /shared/images but its checksum differs; remove it before staging")
    if not profile.image_source:
        raise ValueError("target image is not in /shared/images and image_source is not configured")
    local = None
    if apply or not re.match(r"^https?://", profile.image_source):
        local = local_image(profile, options, emit)
    size = os.path.getsize(local) if local else None
    if size is not None:
        ensure_image_space(client, profile, plan, profile.minimum_free_bytes + size, apply, emit)
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
    name = plan.setdefault("ucs_name", ucs_name(host, profile))
    passphrase = os.environ.get("NETOPS_F5_UCS_PASSPHRASE", "")
    if passphrase:
        from ..debuglog import protect
        protect([passphrase])
    directory = getattr(options, "ucs_dir", None) or Path(options.lock_dir).parent / "ucs-backups"
    emit("backing_up", f"Saving UCS {name} on the unit" + (" and downloading it" if directory else ""))
    f5.save_ucs(client, name, passphrase)
    record = {"name": name, "on_unit": True, "encrypted": bool(passphrase)}
    if directory:
        destination = Path(directory) / name
        record["bytes"] = f5.download_ucs(client, name, destination)
        digest = f5.remote_md5(client, name, f5.UCS_DIR)
        if not digest or digest != f5.local_md5(destination):
            destination.unlink(missing_ok=True)
            raise ValueError("UCS download checksum could not be verified against the archive on the unit")
        # Optional private group sharing for an authenticated NetBox download
        # mount. Unconfigured pollers retain the default owner-only mode.
        group = os.environ.get("NETOPS_UCS_READ_GROUP")
        if group:
            import grp
            os.chown(destination, -1, grp.getgrnam(group).gr_gid)
            os.chmod(destination, 0o640)
        record.update(local_path=str(destination), md5_verified=bool(digest))
    plan["ucs_backup"] = record
    emit("backing_up", f"UCS downloaded and checksum verified: {record['local_path']} ({record['bytes']} bytes)",
         {"upgrade_plan": plan, "progress_summary": {"ucs_backup": record}})


def wait_for_state(client, wanted, options, emit):
    deadline = time.monotonic() + FAILOVER_WAIT
    while True:
        state = f5.facts(client.get_json).get("failover_state")
        if state == wanted:
            return
        if time.monotonic() >= deadline:
            raise ValueError(f"unit did not become {wanted} within {FAILOVER_WAIT}s; failover state is {state}")
        emit("failing_over", f"Waiting to become {wanted}; currently {state}")
        _sleep(options.poll_interval)


def installed_target(info, profile):
    """A stale 'complete' status alone does not prove the requested image is installed."""
    image = F5_IMAGE.fullmatch(profile.image)
    return (str(info.get("status", "")).lower() == "complete"
            and str(info.get("active", False)).lower() == "false"
            and bool(info.get("version")) and f5_version(info["version"]) == f5_version(profile.target_version)
            and image is not None and str(info.get("build", "")) == image[2])


def wait_for_install(client, volume, options, emit, profile):
    deadline, heartbeat = time.monotonic() + options.install_timeout, time.monotonic()
    while True:
        info = f5.volume_info(client, volume)
        status = str(info.get("status", "not yet visible"))
        if str(info.get("active", "")).lower() == "true":
            raise ValueError(f"installation target {volume} unexpectedly became active")
        if installed_target(info, profile):
            return
        if re.search(r"fail|error|abort", status, re.I):
            raise ValueError(f"installation to {volume} reported: {status}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"installation to {volume} did not complete with the target version/build within {int(options.install_timeout)}s; "
                               f"last status {status!r}, version {info.get('version')}, build {info.get('build')}")
        if time.monotonic() - heartbeat >= 30:
            emit("installing", f"Installing to {volume}: {status}; version {info.get('version')}, build {info.get('build')}")
            heartbeat = time.monotonic()
        _sleep(options.poll_interval)


def wait_for_target(host, settings, profile, volume, options, emit):
    """Reconnect after the reboot; returns an open client once the target volume is active."""
    deadline, last = time.monotonic() + options.reload_timeout, "unit has not returned"
    while time.monotonic() < deadline:
        client = None
        try:
            client = f5_waf.Client(host, **settings).__enter__()
            info = f5.facts(client.get_json)
            if info.get("active_volume") == volume and info.get("version") and f5_version(info["version"]) == f5_version(profile.target_version):
                return client
            last = f"unit reachable but active volume is {info.get('active_volume')} on {info.get('version')}"
        except Exception as exc:
            last = type(exc).__name__
        emit("reconnecting", f"Waiting for the unit to boot {volume} on {profile.target_version}; last observation: {last}")
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except Exception:
                pass
        _sleep(options.poll_interval)
    raise TimeoutError(f"reboot deadline exceeded: {last}; manual recovery required")


def target_findings(after, profile, plan):
    findings = []
    info = after.get("facts", {})
    unit = after.get("software", {}).get("1", {})
    if (not unit or unit.get("mode") != plan["volume"]["name"] or not unit.get("version")
            or f5_version(unit["version"]) != f5_version(profile.target_version)):
        findings.append({"check": "software", "severity": "error", "message": "unit is not running the target release from the planned volume"})
    if not info.get("license_service_check_date"):
        preexisting = (plan.get("license_unavailable_before") and info.get("license_unavailable")
                       and not profile.license_check_date)
        findings.append({"check": "license", "severity": "warning" if preexisting else "error",
                         "message": "license was unavailable before the upgrade and remains unavailable; verify licensing before using the device for traffic"
                         if preexisting else "license is not readable after the reboot"})
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

    started = time.monotonic()
    phase_step, phase_started = 1, started
    validation_gate = None
    phases = {"connecting": 1, "saving_config": 1, "precheck": 1, "precheck_complete": 1, "ready": 1,
              "staging": 2, "image_verification": 2, "backing_up": 3, "failing_over": 4, "installing": 4,
              "draining": 4, "rebooting": 5, "reconnecting": 6, "settling": 7,
              "postcheck": 8, "validating": 8, "converging": 8, "waf_requests": 9}
    labels = {1: "Prechecks", 2: "Image staging", 3: "UCS backup", 4: "Drain and install",
              5: "Reboot", 6: "Reconnect", 7: "Settling", 8: "Health validation", 9: "WAF request logs"}

    def emit(stage, message, payload=None, attachment=None):
        nonlocal phase_step, phase_started
        payload = dict(payload or {})
        now = time.monotonic()
        step = (7 if stage == "converging" and phase_step < 8 else phases.get(stage, phase_step))
        if step > phase_step:
            phase_step, phase_started = step, now
        phase = {"step": phase_step, "total": 2 if stage_only else 9, "name": labels[phase_step],
                 "elapsed_seconds": int(now - started), "phase_elapsed_seconds": int(now - phase_started)}
        payload["progress_summary"] = {**payload.get("progress_summary", {}), "phase": phase}
        payload["progress_summary"].setdefault("awaiting_manual_validation", False)
        payload["progress_summary"]["manual_validation_step"] = (
            phase_step if validation_gate == phase_step and stage in ('validating', 'converging', 'waf_requests')
            and cancellation.validation_control() else None)
        if plan.get('validation_overrides'):
            payload["progress_summary"]["validation_overrides"] = dict(plan['validation_overrides'])
        message = f"Step {phase_step}/{phase['total']} · {labels[phase_step]} · {int(now - started)}s elapsed: {message}"
        if plan.get('rollback_plan'):
            payload['progress_summary']['rollback_plan'] = dict(plan['rollback_plan'])
        if plan.get("waf_requests"):
            payload["progress_summary"]["waf_requests"] = dict(plan["waf_requests"])
        if plan.get("connection_drain"):
            payload = dict(payload or {})
            record = plan["connection_drain"]
            summary = {key: value for key, value in record.items()
                       if key not in {"virtual_servers", "restore_pending"}}
            summary["restoration_pending"] = len(record["restore_pending"])
            payload["progress_summary"] = {**payload.get("progress_summary", {}), "connection_drain": summary}
            if plan.get('rollback_note'):
                payload['progress_summary'].update(rollback_note=plan['rollback_note'], rollback_volume=plan['active_volume'])
            if stage == 'validation_failed' and record.get('restore_pending'):
                message += '; virtual servers remain disabled in the saved configuration; manual recovery required'
                payload['upgrade_plan'] = plan
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
                if not stage_only and plan.get('active_volume') and plan.get('volume'):
                    plan['rollback_plan'] = {
                        'volume': plan['active_volume'], 'from_volume': plan['volume']['name'],
                        'previous_version': before.get('facts', {}).get('version'),
                        'previous_build': before.get('facts', {}).get('build'),
                    }
                if profile.drain_connections and not stage_only and not plan["already_current"] and not plan["blockers"]:
                    try:
                        plan["connection_drain"] = f5_drain.prepare(client, profile)
                    except Exception as exc:
                        plan["blockers"].append(f"connection drain: {exc}")
                plan["commands"] = [] if stage_only or "volume" not in plan else planned_commands(profile, plan, host)
                emit("precheck_complete", "Image staging checks captured" if stage_only else "Baseline and upgrade plan captured",
                     {"pre": before, "upgrade_plan": plan,
                      "progress_summary": {"counts": before["metrics"]["counts"], "target_version": profile.target_version,
                                           **({"waf": before["waf"]} if before.get("waf", {}).get("provisioned") else {}),
                                           "starting_versions": plan["starting_versions"], "bundle_conversion": False,
                                           "unsaved_changes": False, "failover_state": plan.get("failover_state")},
                      "rollback_unsupported": ["Staged image files are retained for inspection; there are no configuration changes to roll back." if stage_only
                                               else f"Recovery is rebooting to the previous boot volume ({plan.get('active_volume')}); configuration rollback is not an image rollback."]})
                if plan.get("failover_first") and not stage_only:
                    emit("precheck", "Unit is the active member of its device group; the profile allows failing over before install"
                         if profile.allow_active else "Unit is the active member of its device group")
                if plan.get("license_unavailable_before"):
                    emit("precheck", "License unavailable before this job; verify licensing before using this device for traffic")
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
                if stage_only and plan["image_verification"] == "verified" and not plan.get("image_cleanup"):
                    emit("staged", "Image already present and checksum verified; no upload needed", {"upgrade_plan": plan})
                    return Result(host=host, result=plan)
                if not emit("ready", "Staging checks passed; uploading image only" if stage_only else "Prechecks passed; starting approved upgrade", {"upgrade_plan": plan}):
                    raise ValueError("progress webhook unavailable before apply; no install or reboot performed")
                if plan["image_verification"] == "pending_transfer" or plan.get("image_cleanup"):
                    changed = True
                    verify_image(client, profile, options, plan, apply=True, emit=emit)
                if stage_only:
                    emit("staged", "Image ready in /shared/images and checksum verified", {"upgrade_plan": plan})
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
                reboot_resolved = False
                try:
                    if profile.drain_connections:
                        f5_drain.drain(client, profile, plan["connection_drain"], emit)
                        f5_drain.persist_disabled(client, plan["connection_drain"], emit)
                        plan['rollback_note'] = ('The previous boot volume contains the disabled VIP configuration. '
                                                 'Restore the recorded original VIP states after validating rollback, or restore the pre-upgrade UCS.')
                    reused = installed_target(f5.volume_info(client, volume), profile)
                    plan["volume"]["reused_installation"] = reused
                    if reused:
                        emit("installing", f"{volume} already contains the exact target version/build; refreshing its configuration")
                    else:
                        emit("installing", f"Installing {profile.image} to boot volume {volume}" + (" (new volume)" if plan["volume"]["create"] else ""))
                        f5.install(client, profile.image, volume, plan["volume"]["create"])
                        wait_for_install(client, volume, options, emit, profile)
                    emit("installing", f"Copying saved configuration from {plan['active_volume']} to {volume}; reboot requires confirmed success")
                    plan["configuration_copy"] = f5.copy_configuration(
                        client, plan["active_volume"], volume, options.install_timeout, poll=options.poll_interval,
                        progress=lambda message: emit("installing", message))
                    emit("installing", f"Configuration copy to {volume} completed successfully", {"upgrade_plan": plan})
                    emit("rebooting", f"Installation complete on {volume}; rebooting the unit to it")
                    accepted = f5.reboot_to(client, volume)
                    reboot_resolved = True  # Includes transport loss: target state must now be validated.
                    emit("rebooting", "Reboot request accepted; waiting for the target boot volume" if accepted else
                         "Connection lost during reboot request; outcome uncertain, checking the boot volume without resending")
                except Exception:
                    if not reboot_resolved and plan.get("connection_drain"):
                        f5_drain.release_traffic(client, plan["connection_drain"], emit)
                    raise
            finally:
                close(client)
            # Once reboot was accepted (or its response was lost), never release
            # traffic on an unvalidated target or an unexpected boot volume.
            client = wait_for_target(host, settings, profile, volume, options, emit)
            try:
                drain = plan.get('connection_drain')
                if drain:
                    f5_drain.hold_after_reboot(client, drain, emit)
                f5_requests.mark_reconnected(client, before, plan)
                emit("settling", "Target volume is reachable; waiting before health validation")
                settle(emit, options)
                def finish_validation(after, findings):
                    from . import checks
                    emit('validating', ('Health validation manually passed; restoring traffic'
                                       if plan.get('validation_overrides', {}).get('8') else
                                       'WAF and health checks passed twice; restoring traffic'),
                         {'post_before_restore': after, 'findings_before_restore': findings})
                    try:
                        f5_drain.release_traffic(client, drain, emit)
                        if plan.get('validation_overrides', {}).get('8'):
                            restored = {row['name']: row['enabled'] for row in f5_drain.inventory(client)}
                            if any(restored.get(row['name']) != row['enabled'] for row in drain['virtual_servers']):
                                raise RuntimeError('Original virtual-server states could not be confirmed after restoration')
                        final = f5.collect(client, lambda path: emit('postcheck', path))
                        final_findings = checks.compare(before, final, **thresholds) + target_findings(final, profile, plan)
                        final_findings = f5_validation.findings_after_override(final_findings, plan)
                        emit('validating', 'Final collection after restoring original virtual-server states',
                             {'post': final, 'findings': final_findings,
                              'progress_summary': {'counts': final['metrics']['counts'],
                                                   'finding_count': len(final_findings),
                                                   **({'waf': {'expected_policies': before['waf'].get('policy_count'),
                                                               'observed_policies': final.get('waf', {}).get('policy_count'),
                                                               'ready': final.get('waf', {}).get('ready')}} if before.get('waf', {}).get('provisioned') else {}),
                                                   'error_count': sum(f['severity'] == 'error' for f in final_findings)}})
                        if any(f['severity'] == 'error' for f in final_findings):
                            raise RuntimeError('Validation failed after traffic restoration')
                        return final, final_findings
                    except Exception:
                        # A partial restoration or failing final observation must
                        # not leave traffic enabled on an unhealthy unit.
                        f5_drain.disable_again(client, drain, emit)
                        raise
                def finish_upgrade(after, findings):
                    nonlocal validation_gate
                    # Close the UI gate before moving on. Its report response
                    # carries any operator request that won the race with this
                    # transition, including when checks just passed naturally.
                    validation_gate = None
                    emit('validating', 'Health validation gate finished')
                    f5_validation.forced(plan, 8, emit)
                    findings = f5_validation.findings_after_override(findings, plan)
                    if drain:
                        after, findings = finish_validation(after, findings)
                    # This gate runs outside restoration's failure handler: a
                    # quiet log must not disable traffic that passed health checks.
                    validation_gate = 9
                    f5_requests.wait_for_request(client, after, plan, options, emit)
                    validation_gate = None
                    emit('waf_requests', 'WAF request-log gate finished')
                    if f5_validation.forced(plan, 9, emit):
                        plan['waf_requests']['status'] = 'manually_passed'
                    return after, findings
                validation_gate = 8
                thresholds = {key: getattr(profile, key) for key in set(checks.F5_THRESHOLDS.values())}
                def compare_health(before, after, **kwargs):
                    if drain:
                        return f5_drain.compare_while_drained(before, after, drain, **kwargs, **thresholds)
                    return checks.compare(before, after, **kwargs, **thresholds)
                return converge(emit, options, before, plan, reporter, host,
                                lambda: f5.collect(client, lambda path: emit("postcheck", path)),
                                lambda after: target_findings(after, profile, plan),
                                compare_snapshots=compare_health,
                                on_validated=finish_upgrade,
                                validation_override=lambda **kwargs: f5_validation.forced(plan, 8, emit, **kwargs))
            finally:
                close(client)
    except Exception as exc:
        from ..debuglog import redact
        from ..errors import DeviceHTTPError, summarize
        message = (redact(str(exc)) if isinstance(exc, (ValueError, TimeoutError, RuntimeError))
                   and not isinstance(exc, DeviceHTTPError) else redact(summarize(exc)))
        pending = plan.get("connection_drain", {}).get("restore_pending", [])
        if pending:
            message += "; verify/restore original enabled state manually for: " + ", ".join(pending)
        status = ("staging_failed" if stage_only else "recovery_required") if changed else "failed"
        emit(status, message, {"error": message, "upgrade_plan": plan})
        return Result(host=host, result={"error": message}, failed=True, changed=changed)


def _sleep(seconds):
    return cancellation.sleep(seconds, fallback=time.sleep)
