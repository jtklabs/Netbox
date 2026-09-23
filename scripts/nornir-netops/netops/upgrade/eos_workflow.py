"""Arista EOS upgrade driver over SSH: the IOS XE stages, gates and reporting.

Order of operations under --apply: write memory, baseline, image verified on
flash or copied from image_source and MD5-checked on the switch, the ready
gate, `install source flash:<image> now reload` (EOS writes boot-config and
reloads itself), reconnect, then the shared convergence loop and comparison
report. EOS boots one .swi file: there is no package expansion or stack to
coordinate. An MLAG peer is a separate device and is never reloaded in the same
run by this driver; NetBox redundancy groups keep the two apart.
"""

import difflib
import re
import time
from dataclasses import asdict

from nornir.core.task import Result

from . import checks, eos_checks
from .profile import eos_version
from .workflow import Device, comparison_report, converge, device_lock, settle, stage_image

HEALTHY_MLAG = {"Disabled", "Active"}


def install_command(profile):
    # The image is already on flash and MD5-checked, so install copies nothing:
    # it points boot-config at the image and reloads. `now` skips the prompts.
    return f"install source flash:{profile.image} now reload"


class EosDevice(Device):
    reload_success = re.compile(r"going down for reboot|Broadcast message", re.I)

    def scp_destination(self, image):
        return f"/mnt/flash/{image}"

    def delete_command(self, image):
        return f"delete flash:{image}"

    def answer(self, tail, reload):
        # `install ... now reload` normally reloads without asking; answer the
        # plain confirmation if it appears. A save prompt means the running
        # configuration changed after it was verified saved: stop instead.
        if reload and re.search(r"Proceed with reload\?\s*\[confirm\]\s*$", tail, re.I):
            return "\n"
        return None

    def wait_for_target(self, profile):
        self.close()
        deadline = time.monotonic() + self.options.reload_timeout
        last_error = "device has not returned"
        while time.monotonic() < deadline:
            self.emit("reconnecting", f"Waiting for EOS {profile.target_version}")
            try:
                self.connect()
                unit = eos_checks.software(self.read("show version"))["1"]
                if eos_version(unit["version"]) == eos_version(profile.target_version):
                    return
                last_error = f"device reachable but running {unit['version']}"
            except Exception as exc:
                last_error = type(exc).__name__
            self.close()
            time.sleep(self.options.poll_interval)
        raise TimeoutError(f"reload deadline exceeded: {last_error}; manual recovery required")


def preflight(snapshot, profile, stage_only=False, saved=False, allow_mismatch=False):
    blockers = [f"{key}: {value}" for key, value in snapshot["errors"].items()]
    unit = snapshot.get("software", {}).get("1", {})
    current = unit.get("version")
    if unit and unit.get("model") not in profile.models:
        blockers.append("hardware model is not approved by this profile")
    target = eos_version(profile.target_version)
    at_target = bool(current) and eos_version(current) == target
    if current and profile.starting_versions and not at_target and eos_version(current) not in {eos_version(v) for v in profile.starting_versions}:
        blockers.append("current release is not an approved starting version")
    boot = snapshot.get("boot", {}).get("image")
    mlag = (snapshot.get("tables", {}).get("mlag") or [{}])[0]
    plan = {"blockers": blockers, "bundle_conversion": False, "upgrade_needed": not at_target,
            "already_current": at_target and boot == profile.image,
            "starting_versions": [current] if current else [], "target_version": profile.target_version,
            "profile": profile.name, "approved_profile": asdict(profile), "operation": "stage_image" if stage_only else "upgrade",
            "boot_image": boot, "mlag_state": mlag.get("state")}
    if stage_only:
        return plan
    if at_target and boot != profile.image:
        blockers.append(f"target is running but boot-config points at {boot or 'no image'}; a reload would not boot the target")
    state = mlag.get("state")
    if state and state not in HEALTHY_MLAG:
        blockers.append(f"MLAG state is {state}; expected Active or Disabled")
    if state == "Active":
        if mlag.get("peer_config", "consistent") != "consistent":
            blockers.append("MLAG peer configuration is not consistent")
        if mlag.get("peer_link_status") != "Up" or mlag.get("negotiation_status") != "Connected":
            blockers.append("MLAG peer link is not up and negotiated")
    # --apply saves the running configuration before the two configurations are
    # read, so a difference is informational on a dry run and an error once a
    # save has been made. A failed read is already reported by its own error.
    plan["unsaved_changes"] = ("config" in snapshot and "startup_config" in snapshot
                               and snapshot["config"] != snapshot["startup_config"])
    plan["config_mismatch_overridden"] = False
    if plan["unsaved_changes"]:
        plan["saved_config_diff"] = "\n".join(difflib.unified_diff(
            snapshot["startup_config"].splitlines(), snapshot["config"].splitlines(),
            fromfile="startup-config", tofile="running-config", lineterm=""))
        if saved and allow_mismatch:
            plan["config_mismatch_overridden"] = True
        elif saved:
            blockers.append("running/startup configuration still differ after write memory; "
                            "see upgrade_plan.saved_config_diff in the local report")
    return plan


def image_size(listing, image):
    match = re.search(r"(?m)^\s*[-d][rwx-]+\s+(\d+)\s+.*\s" + re.escape(image) + r"\s*$", listing)
    return int(match[1]) if match else None


def verify_image(device, profile, snapshot, plan, apply):
    """Only read in preview; copy and verify the .swi on flash under apply."""
    image = f"flash:{profile.image}"
    output = device.read(f"dir {image}")
    size = image_size(output, profile.image)
    if size is None and not re.search(r"No such file|not found|No files|Error opening|does not exist", output, re.I):
        raise ValueError("could not determine whether target image exists")
    exists = size is not None
    plan["image_size_bytes"] = size
    listing = checks.understood("dir flash:", device.read("dir flash:"))
    free = re.search(r"\((\d+) bytes free\)", listing)
    needed = profile.minimum_free_bytes + (0 if exists else (size or 0))
    plan["flash"] = {"1": {"free_bytes": int(free[1]) if free else None, "required_free_bytes": needed}}
    if not free:
        raise ValueError("free flash space could not be read from dir flash:")
    if int(free[1]) < needed:
        raise ValueError(f"insufficient free flash space: {free[1]} bytes free, {needed} required "
                         f"({profile.minimum_free_bytes} reserve{'' if exists else ' plus the image'})")
    if not exists:
        if not profile.image_source:
            raise ValueError("target image missing from flash and image_source is not configured")
        plan["image_verification"] = "pending_transfer"
        plan["staging_command"] = f"copy {profile.image_source} {image}"
        if not apply:
            return
        stage_image(device, profile, plan, lambda: image_size(device.read(f"dir {image}"), profile.image) is not None)
    device.emit("image_verification", "Checking Arista image checksum")
    output = device.write(f"verify /md5 {image}", timeout=device.options.install_timeout)
    hashes = re.findall(r"\b[a-fA-F0-9]{32}\b", output)
    if not hashes or any(value.lower() != profile.md5.lower() for value in hashes):
        raise ValueError("image checksum does not match approved profile")
    plan["image_verification"] = "verified"
    if not exists:
        # After a transfer, check the reserve again with the real file size.
        size = image_size(device.read(f"dir {image}"), profile.image)
        if size is None:
            raise ValueError("could not determine transferred image size")
        plan["image_size_bytes"] = size
        free = re.search(r"\((\d+) bytes free\)", device.read("dir flash:"))
        if not free or int(free[1]) < profile.minimum_free_bytes:
            raise ValueError(f"insufficient reserve space after transfer: {free[1] if free else 'unknown'} bytes free, "
                             f"{profile.minimum_free_bytes} required")


def target_findings(snapshot, profile, before, saved=True):
    findings = []
    unit = snapshot.get("software", {}).get("1", {})
    if (not unit or not unit.get("version") or eos_version(unit["version"]) != eos_version(profile.target_version)
            or unit.get("model") != before.get("software", {}).get("1", {}).get("model")):
        findings.append({"check": "software", "severity": "error", "message": "unit is not running the target release"})
    if snapshot.get("boot", {}).get("image") != profile.image:
        findings.append({"check": "boot", "severity": "error", "message": "boot-config does not point at the target image"})
    if saved and snapshot.get("config") != snapshot.get("startup_config"):
        findings.append({"check": "saved_config", "severity": "error", "message": "running/startup configuration differ after upgrade"})
    cause = snapshot.get("raw", {}).get("show reload cause", "")
    if isinstance(cause, str) and cause.strip() and not re.search(r"Reload requested by the user", cause, re.I):
        findings.append({"check": "reload_cause", "severity": "warning", "message": "the last reload cause is not the requested user reload"})
    return findings


def upgrade_device(task, profile, options, reporter):
    changed, plan = False, {}
    host = task.host
    stage_only = getattr(options, "stage_only", False)
    allow_mismatch = getattr(options, "allow_config_mismatch", False)

    def emit(stage, message, payload=None, attachment=None):
        return reporter.emit(host, stage, message, payload, changed=changed, attachment=attachment)

    device = EosDevice(task, options, emit)
    try:
        with device_lock(options.lock_dir, f"{host.hostname}:{host.port or 22}"):
            if host.platform not in {None, "", "arista_eos"}:
                raise ValueError("an EOS profile requires an arista_eos device")
            host.platform = "arista_eos"
            emit("connecting", "Connecting for image staging checks" if stage_only else "Connecting for pre-upgrade verification")
            device.connect()
            configuration_saved = False
            if options.apply and not stage_only:
                emit("saving_config", "Saving running configuration before comparing it with startup-config")
                if "Copy completed successfully" not in device.write("write memory"):
                    raise ValueError("configuration save was not acknowledged")
                configuration_saved = True
            collect = eos_checks.collect_staging if stage_only else eos_checks.collect
            before = collect(device.read, lambda cmd: emit("precheck", cmd))
            plan = preflight(before, profile, stage_only=stage_only, saved=configuration_saved, allow_mismatch=allow_mismatch)
            plan["configuration_saved"] = configuration_saved
            plan["commands"] = [] if stage_only else ["write memory", install_command(profile)]
            emit("precheck_complete", "Image staging checks captured" if stage_only else "Baseline and upgrade plan captured",
                 {"pre": before, "upgrade_plan": plan,
                  "progress_summary": {"counts": before["metrics"]["counts"], "target_version": profile.target_version,
                                       "starting_versions": plan["starting_versions"], "bundle_conversion": False,
                                       "unsaved_changes": plan.get("unsaved_changes", False), "mlag_state": plan.get("mlag_state")},
                  "rollback_unsupported": ["Staged image files are retained for inspection; there are no configuration changes to roll back." if stage_only
                                           else f"Recovery is pointing boot-config back at the previous image ({plan.get('boot_image')}) and reloading; configuration rollback is not an image rollback."]})
            if plan.get("unsaved_changes"):
                emit("unsaved_changes", ("Running configuration still differs from startup-config after write memory"
                                         + ("; continuing because --allow-config-mismatch is set" if plan["config_mismatch_overridden"] else ""))
                     if configuration_saved else "Running configuration has unsaved changes; --apply saves them at the start of prechecks")
            if plan["already_current"] and not plan["blockers"] and not stage_only:
                emit("already_current", f"Already running {profile.target_version} with boot-config set to {profile.image}")
                return Result(host=host, result=plan)
            # Keep evaluating image readiness even when the release is blocked.
            # Never copy an image until all baseline gates pass.
            try:
                verify_image(device, profile, before, plan, apply=False)
            except Exception as exc:
                plan["blockers"].append(str(exc))
            if stage_only and "staging_command" in plan:
                plan["commands"] = [plan["staging_command"]]
            if plan["blockers"]:
                emit("blocked", "; ".join(plan["blockers"]), {"upgrade_plan": plan})
                return Result(host=host, result=plan, failed=True)
            if not options.apply:
                message = (("Image verified on flash" if plan["image_verification"] == "verified" else "Image missing; copy planned") if stage_only
                           else "Prechecks passed; upgrade planned; --apply saves the unsaved running configuration first" if plan.get("unsaved_changes")
                           else "Prechecks passed; upgrade planned")
                emit("dry_run_complete", message, {"upgrade_plan": plan})
                return Result(host=host, result=plan)
            if stage_only and plan["image_verification"] == "verified":
                emit("staged", "Image already present and checksum verified; no copy needed", {"upgrade_plan": plan})
                return Result(host=host, result=plan)
            # Delivery failure at the final gate blocks boot and reload changes.
            if not emit("ready", "Staging checks passed; copying image only" if stage_only else "Prechecks passed; starting approved upgrade", {"upgrade_plan": plan}):
                raise ValueError("progress webhook unavailable before apply; no boot or reload changes made")
            if plan["image_verification"] == "pending_transfer":
                changed = True
                verify_image(device, profile, before, plan, apply=True)
            if stage_only:
                emit("staged", "Image copied to flash and checksum verified", {"upgrade_plan": plan})
                return Result(host=host, result=plan, changed=changed)
            # Refuse stale config/version authorizations immediately before the
            # install, which writes boot-config and reloads in one command.
            running = eos_checks.normalized_config(device.read("show running-config"))
            if running != before["config"] or eos_checks.software(device.read("show version")) != before["software"]:
                raise ValueError("configuration or software changed since precheck; rerun required")
            if not allow_mismatch and running != eos_checks.normalized_config(device.read("show startup-config")):
                raise ValueError("running and startup configuration differ before install; install not started")
            changed = True
            emit("installing", f"Installing {profile.image} and reloading")
            try:
                device.interactive(install_command(profile), options.install_timeout, reload=True)
            except (TimeoutError, OSError, EOFError):
                emit("reconnecting", "Install dialogue interrupted; checking outcome without resending install")
            device.wait_for_target(profile)
            settle(emit, options)
            return converge(emit, options, before, plan, reporter, host,
                            lambda: eos_checks.collect(device.read, lambda cmd: emit("postcheck", cmd), before["routing_commands"]),
                            lambda after: target_findings(after, profile, before, saved=not allow_mismatch))
    except Exception as exc:
        from ..debuglog import redact
        message = redact(str(exc)) if isinstance(exc, (ValueError, TimeoutError)) else type(exc).__name__
        status = ("staging_failed" if stage_only else "recovery_required") if changed else "failed"
        emit(status, message, {"error": message, "upgrade_plan": plan})
        return Result(host=host, result={"error": message}, failed=True, changed=changed)
    finally:
        device.close()
