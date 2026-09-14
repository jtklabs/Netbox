"""One independently recoverable Nornir task per switch or management stack."""

import difflib
import fcntl
import hashlib
import re
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from nornir.core.task import Result

from . import checks, report
from .profile import version

BOOT_COMMANDS = ["no boot system", "boot system flash:packages.conf", "no boot manual"]
INSTALL_FAILURE = re.compile(r"(?im)(?:^\s*(?:FAILED:|ERROR:|%Error)|\b(?:install_add_activate_commit|install_add|install_activate|install_commit):\s*(?:FAIL|ERROR)|\b(?:FAILED|ABORTED)\b|\bfailed to\b)")


@contextmanager
def device_lock(directory, address):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (hashlib.sha256(address.encode()).hexdigest() + ".lock")
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another local upgrade run already holds this device") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def preflight(snapshot, profile, stage_only=False, saved=False, allow_mismatch=False):
    blockers = [f"{key}: {value}" for key, value in snapshot["errors"].items()]
    members = snapshot.get("software", {})
    stack = snapshot.get("stack", {})
    if set(members) != set(stack) or not members:
        blockers.append("software and stack member lists do not agree")
    if any(row["state"].lower() != "ready" for row in stack.values()):
        blockers.append("not every stack member is Ready")
    versions = {version(row["version"]) for row in members.values()}
    modes = {row["mode"] for row in members.values()}
    if len(versions) != 1 or len(modes) != 1:
        blockers.append("mixed or missing stack versions/boot modes")
    if any(row["model"] not in profile.models for row in members.values()):
        blockers.append("hardware PID is not approved by this profile")
    target = version(profile.target_version)
    at_target = versions == {target}
    conversion = "BUNDLE" in modes
    if not at_target and not versions <= {version(v) for v in profile.starting_versions}:
        blockers.append("current release is not an approved starting version")
    plan = {"blockers": blockers, "bundle_conversion": conversion,
            "upgrade_needed": not at_target, "already_current": at_target and not conversion,
            "starting_versions": sorted(row["version"] for row in members.values()),
            "target_version": profile.target_version, "profile": profile.name,
            "approved_profile": asdict(profile), "operation": "stage_image" if stage_only else "upgrade"}
    if stage_only:
        return plan
    if conversion and not profile.bundle_conversion_validated:
        blockers.append("BUNDLE mode: conversion is not validated in this profile")
    if at_target and conversion:
        blockers.append("already at target in BUNDLE mode; requires a separately validated conversion path")
    if at_target and not conversion and boot_findings(snapshot, saved=False):
        blockers.append("target is running but saved install-mode autoboot settings are not healthy")
    # --apply saves the running configuration before the two configurations are
    # read, so a difference is informational on a dry run and an error once a
    # save has been made. A failed read is already reported by its own error.
    plan["unsaved_changes"] = ("config" in snapshot and "startup_config" in snapshot
                               and snapshot["config"] != snapshot["startup_config"])
    plan["config_mismatch_overridden"] = False
    if plan["unsaved_changes"]:
        # Keep config evidence in the private archive, not progress messages.
        plan["saved_config_diff"] = "\n".join(difflib.unified_diff(
            snapshot["startup_config"].splitlines(), snapshot["config"].splitlines(),
            fromfile="startup-config", tofile="running-config", lineterm=""))
        if saved and allow_mismatch:
            plan["config_mismatch_overridden"] = True
        elif saved:
            blockers.append("running/startup configuration still differ after write memory; "
                            "see upgrade_plan.saved_config_diff in the local report")
    for cmd in ("show boot", "show install summary"):
        if cmd in snapshot["warnings"]:
            blockers.append(f"required install preflight unavailable: {cmd}")
    summary = snapshot["raw"].get("show install summary", "")
    if re.search(r"(?m)^\s*IMG\s+[UAID]\s+", summary):
        blockers.append("install summary contains uncommitted/inactive/deactivated image state")
    if re.search(r"Auto abort timer:\s*active\b", summary, re.I):
        blockers.append("an install auto-abort timer is already active")
    if not conversion:
        installed = committed_members(summary)
        if set(installed) != set(members) or any(not release_matches(installed.get(key, ""), row["version"]) for key, row in members.items()):
            blockers.append("committed starting image cannot be confirmed on every stack member")
    return plan


class Device:
    def __init__(self, task, options, emit):
        self.task = task
        self.options = options
        self.emit = emit
        self.connection = None

    def connect(self):
        self.connection = self.task.host.get_connection("netmiko", self.task.nornir.config)
        self.connection.enable()
        return self.connection

    def close(self):
        if self.connection is not None:
            try:
                self.task.host.close_connection("netmiko")
            except Exception:
                pass
            self.connection = None

    def read(self, command):
        timeout = self.options.show_timeout
        if command in ("show running-config", "show startup-config"):
            # Configuration dumps are the slowest reads on a large stack.
            timeout = max(timeout, getattr(self.options, "config_timeout", 0) or 0)
        return self.connection.send_command(command, read_timeout=timeout)

    def write(self, command, timeout=120):
        output = self.connection.send_command(command, read_timeout=timeout)
        checks.understood(command, output)
        if INSTALL_FAILURE.search(output):
            raise ValueError(f"device reported failure for {command}")
        return output

    def interactive(self, command, timeout, reload=False):
        """Read a bounded channel dialogue. Only answer recognized prompts.

        Once installation may have begun, never retry it on a transport error.
        A lost session is resolved through fresh post-reload observations.
        """
        conn = self.connection
        prompt = conn.find_prompt().strip()
        conn.write_channel(command + "\n")
        deadline, heartbeat = time.monotonic() + timeout, time.monotonic()
        transcript, answered = "", 0
        try:
            while time.monotonic() < deadline:
                chunk = conn.read_channel()
                transcript += chunk
                checks.understood(command, transcript)
                if INSTALL_FAILURE.search(transcript):
                    raise ValueError("device reported install/copy failure")
                tail = transcript[answered:]
                if reload and re.search(r"(?:Do you want to proceed|proceed with reload|reload of the system).*?\[y/n\]\s*[:?]?\s*$", tail, re.I | re.S):
                    conn.write_channel("y\n")
                    answered = len(transcript)
                elif reload and re.search(r"Please confirm you have changed boot config to flash:packages\.conf\s*\[y/n\]\s*$", tail, re.I):
                    # The workflow verifies both running and saved boot settings
                    # before entering this dialogue (also used for conversion).
                    conn.write_channel("y\n")
                    answered = len(transcript)
                elif not reload and re.search(r"Destination filename\s*\[[^\]\r\n]*\]\?\s*$", tail, re.I):
                    conn.write_channel("\n")
                    answered = len(transcript)
                elif re.search(r"(?:\[y/n\]|\[confirm\]|[Pp]assword:|[Uu]sername:)\s*[:?]?\s*$", tail):
                    raise ValueError("unrecognized interactive prompt; manual inspection required")
                if re.search(r"(?:^|\n)" + re.escape(prompt) + r"\s*$", transcript):
                    if reload and not re.search(r"SUCCESS|Install will reload|Reloading", transcript, re.I):
                        raise ValueError("install returned to prompt without success evidence")
                    return transcript
                if not conn.is_alive():
                    if reload:
                        return transcript
                    raise ValueError("connection lost during image copy")
                if time.monotonic() - heartbeat >= 30:
                    self.emit("installing" if reload else "staging", "Operation still running")
                    heartbeat = time.monotonic()
                time.sleep(1)
        except (OSError, EOFError):
            if reload:
                return transcript
            raise
        finally:
            # Capture even interrupted/ambiguous dialogues without putting
            # command output or configs into the external progress payload.
            self.emit("installing" if reload else "staging", "Command dialogue captured",
                      {"install_output" if reload else "copy_output": transcript})
        raise TimeoutError("install/copy dialogue timed out; do not automatically retry")

    def wait_for_target(self, profile):
        self.close()
        deadline = time.monotonic() + self.options.reload_timeout
        last_error = "device has not returned"
        while time.monotonic() < deadline:
            self.emit("reconnecting", "Waiting for target IOS XE release in INSTALL mode")
            try:
                self.connect()
                members = checks.software(self.read("show version"))
                if all(version(row["version"]) == version(profile.target_version) and row["mode"] == "INSTALL" for row in members.values()):
                    return
                last_error = "device reachable but not running the target in INSTALL mode"
            except Exception as exc:
                last_error = type(exc).__name__
            self.close()
            time.sleep(self.options.poll_interval)
        raise TimeoutError(f"reload deadline exceeded: {last_error}; manual recovery required")


def verify_image(device, profile, snapshot, plan, apply):
    """Only read in preview. The source image is auto-copied by install to members."""
    image = f"flash:{profile.image}"
    output = device.read(f"dir {image}")
    exists = bool(re.search(r"\b" + re.escape(profile.image) + r"\s*$", output, re.M))
    if not exists and not re.search(r"No such file|Error opening|File not found", output, re.I):
        raise ValueError("could not determine whether target image exists")
    size_match = re.search(r"(?m)^\s*\d+\s+-[rwx-]+\s+(\d+)\s+.*" + re.escape(profile.image) + r"\s*$", output) if exists else None
    if exists and not size_match:
        raise ValueError("could not determine target image size")
    size = int(size_match[1]) if size_match else 0
    plan["image_size_bytes"] = size or None
    for member in snapshot["stack"]:
        command = f"dir flash-{member}:"
        listing = checks.understood(command, device.read(command))
        free = re.search(r"(\d+) bytes free", listing)
        plan.setdefault("flash", {})[member] = {"raw": listing, "free_bytes": int(free[1]) if free else None}
        needed = profile.minimum_free_bytes + (0 if profile.image in listing else size)
        plan["flash"][member]["required_free_bytes"] = needed
        if not free or int(free[1]) < needed:
            raise ValueError(f"member {member}: insufficient or unknown free flash space")
    if not exists:
        if not profile.image_source:
            raise ValueError("target image missing from active flash and image_source is not configured")
        plan["image_verification"] = "pending_transfer"
        plan["staging_command"] = f"copy {profile.image_source} {image}"
        if not apply:
            return
        device.emit("staging", "Copying target image to active flash")
        device.interactive(f"copy {profile.image_source} {image}", device.options.install_timeout)
    device.emit("image_verification", "Checking Cisco image checksum")
    output = device.write(f"verify /md5 {image}", timeout=device.options.install_timeout)
    hashes = re.findall(r"\b[a-fA-F0-9]{32}\b", output)
    if not hashes or any(value.lower() != profile.md5.lower() for value in hashes):
        raise ValueError("image checksum does not match approved profile")
    plan["image_verification"] = "verified"
    # After a transfer, check the expansion reserve again before boot changes.
    if not exists:
        listing = device.read(f"dir {image}")
        size_match = re.search(r"(?m)^\s*\d+\s+-[rwx-]+\s+(\d+)\s+.*" + re.escape(profile.image) + r"\s*$", listing)
        if not size_match:
            raise ValueError("could not determine transferred image size")
        size = int(size_match[1])
        plan["image_size_bytes"] = size
        for member in snapshot["stack"]:
            listing = device.read(f"dir flash-{member}:")
            free = re.search(r"(\d+) bytes free", listing)
            needed = profile.minimum_free_bytes + (0 if profile.image in listing else size)
            if not free or int(free[1]) < needed:
                raise ValueError(f"member {member}: insufficient expansion space after transfer")


def target_findings(snapshot, profile, before, saved=True):
    findings = []
    members = snapshot.get("software", {})
    if set(members) != set(before.get("software", {})) or not all(
        row["mode"] == "INSTALL" and version(row["version"]) == version(profile.target_version)
        and row["model"] == before["software"].get(key, {}).get("model") for key, row in members.items()
    ):
        findings.append({"check": "software", "severity": "error", "message": "not every original member is running the target in INSTALL mode"})
    summary = snapshot["raw"].get("show install summary", "")
    committed = committed_members(summary)
    if (set(committed) != set(before.get("software", {})) or any(not release_matches(value, profile.target_version) for value in committed.values())
            or re.search(r"(?m)^\s*IMG\s+[UAID]\s+", summary)):
        findings.append({"check": "install_commit", "severity": "error", "message": "target image commit was not confirmed"})
    findings.extend(boot_findings(snapshot, saved=saved))
    return findings


def boot_findings(snapshot, saved=True):
    findings = []
    for key in ("config", "startup_config"):
        boot = re.findall(r"(?m)^boot system (.+)$", snapshot.get(key, ""))
        if boot not in (["flash:packages.conf"], ["switch all flash:packages.conf"]) or re.search(r"(?m)^boot manual", snapshot.get(key, "")):
            findings.append({"check": key + "_boot", "severity": "error", "message": "saved autoboot settings are not the intended packages.conf settings"})
    if saved and snapshot.get("config") != snapshot.get("startup_config"):
        findings.append({"check": "saved_config", "severity": "error", "message": "running/startup configuration differ after upgrade"})
    return findings


def committed_members(summary):
    members, committed = [], {}
    for line in summary.splitlines():
        header = re.search(r"\[\s*Switch\s+([\d\s]+)\]", line)
        if header:
            members = header[1].split()
        image = re.match(r"\s*IMG\s+C\s+(\S+)", line)
        if image:
            for member in members:
                if member in committed:
                    return {}  # Multiple committed IMG rows are ambiguous.
                committed[member] = image[1]
    return committed


def release_matches(value, target):
    match = re.match(r"(\d+\.\d+\.\d+[a-z]?)", value)
    return bool(match and version(match[1]) == version(target))


def settle(emit, options):
    """Give services time to reconverge after the reload before the first comparison."""
    emit("converging", "Target is reachable; allowing services to reconverge")
    remaining = options.settle_seconds
    while remaining > 0:
        pause = min(30, remaining)
        time.sleep(pause)
        remaining -= pause
        emit("converging", f"Initial settling time remaining: {remaining}s")


def converge(emit, options, before, plan, reporter, host, collect_after, extra_findings):
    """Compare fresh baselines until two consecutive clean passes, or the deadline.

    Every event says why validation has not passed: the checks that differ,
    the clean passes still needed and the time left.
    """
    deadline, consecutive, attempt = time.monotonic() + options.validation_timeout, 0, 0
    while True:
        attempt += 1
        after = collect_after()
        findings = checks.compare(before, after) + extra_findings(after)
        errors = [f for f in findings if f["severity"] == "error"]
        consecutive = 0 if errors else consecutive + 1
        reasons = checks.describe_findings(errors)
        seconds_left = max(0, int(deadline - time.monotonic()))
        emit("validating", f"Comparison {attempt}: " + ("no differences from baseline" if not errors
                                                        else f"{len(errors)} check(s) differ from baseline"),
             {"post": after, "findings": findings,
              "progress_summary": {"counts": after["metrics"]["counts"], "finding_count": len(findings),
                                   "error_count": len(errors), "attempt": attempt, "consecutive_clean": consecutive,
                                   "seconds_remaining": seconds_left,
                                   "pending": [checks.describe_finding(f) for f in errors[:10]]}})
        if consecutive >= 2:
            status = "completed_with_warnings" if findings else "completed"
            fields, attachment = comparison_report(reporter, host, before, after, findings, plan, status)
            emit(status, "Target installed and baseline restored", {"upgrade_plan": plan, **fields}, attachment)
            return Result(host=host, result={"status": status, "findings": findings}, changed=True)
        if time.monotonic() >= deadline:
            fields, attachment = comparison_report(reporter, host, before, after, findings, plan, "validation_failed")
            emit("validation_failed", f"Upgrade returned, but baseline validation did not pass within "
                                      f"{int(options.validation_timeout)}s after settling; still differing: {reasons}",
                 {"findings": findings, **fields}, attachment)
            return Result(host=host, result={"findings": findings}, failed=True, changed=True)
        if errors:
            emit("converging", f"Still differs from baseline after comparison {attempt}: {reasons}. "
                               f"Needs 2 consecutive clean comparisons; {seconds_left}s left before validation fails")
        else:
            emit("converging", f"Comparison {attempt} matched the baseline; one more clean comparison needed "
                               f"({seconds_left}s left)")
        time.sleep(options.poll_interval)


def comparison_report(reporter, host, before, after, findings, plan, status):
    """Build the readable before/after report; a report problem never changes the outcome.

    Returns the archive fields for the final event and the webhook-only attachment.
    """
    try:
        built = report.generate(reporter, host, before, after, findings, plan, status)
    except Exception as exc:
        from ..debuglog import redact
        print(f"{host.name}: comparison report could not be written: {redact(str(exc))}", flush=True)
        return {}, None
    fields = {}
    if built.path is not None:
        print(f"{host.name}: comparison report written to {built.path}", flush=True)
        fields["diff_report"] = str(built.path)
    attachment = {"report": {"format": "text/html", "html": built.html, "markdown": built.markdown,
                             "path": str(built.path) if built.path else None}}
    return fields, attachment


def upgrade_device(task, profile, options, reporter):
    if profile.family == "f5":
        from . import f5_workflow
        return f5_workflow.upgrade_device(task, profile, options, reporter)
    changed = False
    plan = {}
    stage_only = getattr(options, "stage_only", False)
    allow_mismatch = getattr(options, "allow_config_mismatch", False)

    def emit(stage, message, payload=None, attachment=None):
        return reporter.emit(task.host, stage, message, payload, changed=changed, attachment=attachment)

    device = Device(task, options, emit)
    try:
        with device_lock(options.lock_dir, f"{task.host.hostname}:{task.host.port or 22}"):
            if task.host.platform not in {None, "", "cisco_ios"}:
                raise ValueError("upgrade driver requires cisco_ios")
            # A blank platform uses the IOS driver only for read-only discovery;
            # observed per-member PIDs still have to pass the exact model gate.
            task.host.platform = "cisco_ios"
            emit("connecting", "Connecting for image staging checks" if stage_only else "Connecting for pre-upgrade verification")
            device.connect()
            configuration_saved = False
            if options.apply and not stage_only:
                # The upgrade persists the running configuration before install
                # anyway. Saving first turns the running/startup comparison into
                # a check that the save took, rather than a blocker on unsaved work.
                emit("saving_config", "Saving running configuration before comparing it with startup-config")
                if "[OK]" not in device.write("write memory"):
                    raise ValueError("configuration save was not acknowledged")
                configuration_saved = True
            collect = checks.collect_staging if stage_only else checks.collect
            before = collect(device.read, lambda cmd: emit("precheck", cmd))
            plan = preflight(before, profile, stage_only=stage_only, saved=configuration_saved, allow_mismatch=allow_mismatch)
            plan["configuration_saved"] = configuration_saved
            plan["commands"] = [] if stage_only else ["write memory"] + BOOT_COMMANDS + ["write memory", f"install add file flash:{profile.image} activate commit"]
            emit("precheck_complete", "Image staging checks captured" if stage_only else "Baseline and upgrade plan captured", {"pre": before, "upgrade_plan": plan,
                 "progress_summary": {"counts": before["metrics"]["counts"], "target_version": profile.target_version,
                                      "starting_versions": plan["starting_versions"], "bundle_conversion": plan["bundle_conversion"],
                                      "unsaved_changes": plan.get("unsaved_changes", False)},
                 "rollback_unsupported": ["Staged image files are retained for inspection; there are no configuration changes to roll back." if stage_only else "IOS XE upgrades require a separately validated recovery/downgrade procedure; configuration rollback is not an image rollback."]})
            if plan["bundle_conversion"]:
                emit("bundle_mode_flagged", "BUNDLE mode detected; staging will leave it unchanged" if stage_only else "Device requires BUNDLE to INSTALL conversion")
            if plan.get("unsaved_changes"):
                emit("unsaved_changes", ("Running configuration still differs from startup-config after write memory"
                                         + ("; continuing because --allow-config-mismatch is set" if plan["config_mismatch_overridden"] else ""))
                     if configuration_saved else "Running configuration has unsaved changes; --apply saves them at the start of prechecks")
            if plan["already_current"] and not plan["blockers"] and not stage_only:
                emit("already_current", "Already running target release in INSTALL mode")
                return Result(host=task.host, result=plan)
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
                return Result(host=task.host, result=plan, failed=True)
            if not options.apply:
                message = (("Image verified on active flash" if plan["image_verification"] == "verified" else "Image missing; copy planned") if stage_only
                           else "Prechecks passed; upgrade planned; --apply saves the unsaved running configuration first" if plan.get("unsaved_changes")
                           else "Prechecks passed; upgrade planned")
                emit("dry_run_complete", message, {"upgrade_plan": plan})
                return Result(host=task.host, result=plan)
            if stage_only and plan["image_verification"] == "verified":
                emit("staged", "Image already present and checksum verified; no copy needed", {"upgrade_plan": plan})
                return Result(host=task.host, result=plan)
            # Delivery failure at the final gate blocks boot and install changes.
            # After starting, persist locally and continue recovery even if the UI is down.
            if not emit("ready", "Staging checks passed; copying image only" if stage_only else "Prechecks passed; starting approved upgrade", {"upgrade_plan": plan}):
                raise ValueError("progress webhook unavailable before apply; no boot or install changes made")
            if plan["image_verification"] == "pending_transfer":
                changed = True
                verify_image(device, profile, before, plan, apply=True)
            if stage_only:
                emit("staged", "Image copied to active flash and checksum verified", {"upgrade_plan": plan})
                return Result(host=task.host, result=plan, changed=changed)
            # A baseline may have taken minutes. Refuse stale config/version
            # authorizations immediately before the first boot-setting write.
            if (checks.normalized_config(device.read("show running-config")) != before["config"]
                    or checks.software(device.read("show version")) != before["software"]):
                raise ValueError("configuration or software changed since precheck; rerun required")
            changed = True
            emit("configuring_boot", "Setting packages.conf autoboot and saving configuration")
            output = device.connection.send_config_set(BOOT_COMMANDS, read_timeout=options.show_timeout,
                                                        error_pattern=r"(?im)^\s*%\s*(?:Invalid|Error|Incomplete|Ambiguous|Authorization)")
            checks.understood("boot configuration", output)
            saved = device.write("write memory")
            if "[OK]" not in saved:
                raise ValueError("configuration save was not acknowledged")
            saved_state = {"config": checks.normalized_config(device.read("show running-config")),
                           "startup_config": checks.normalized_config(device.read("show startup-config"))}
            if boot_findings(saved_state, saved=not allow_mismatch):
                raise ValueError("saved packages.conf/autoboot settings could not be verified; install not started")
            if checks.normalized_config(saved_state["config"], boot=True) != checks.normalized_config(before["config"], boot=True):
                raise ValueError("unexpected configuration change during boot preparation; install not started")
            emit("installing", "Installing target image; switch/stack will reload")
            try:
                device.interactive(plan["commands"][-1], options.install_timeout, reload=True)
            except (TimeoutError, OSError, EOFError):
                emit("reconnecting", "Install dialogue interrupted; checking outcome without resending install")
            device.wait_for_target(profile)
            settle(emit, options)
            return converge(emit, options, before, plan, reporter, task.host,
                            lambda: checks.collect(device.read, lambda cmd: emit("postcheck", cmd), before["routing_commands"]),
                            lambda after: target_findings(after, profile, before, saved=not allow_mismatch))
    except Exception as exc:
        # Transport exceptions may embed passwords; the archive and debuglog
        # redactor know login secrets. Do not expose raw exceptions in the UI.
        from ..debuglog import redact
        message = redact(str(exc)) if isinstance(exc, (ValueError, TimeoutError)) else type(exc).__name__
        status = ("staging_failed" if stage_only else "recovery_required") if changed else "failed"
        emit(status, message, {"error": message, "upgrade_plan": plan})
        return Result(host=task.host, result={"error": message}, failed=True, changed=changed)
    finally:
        device.close()
