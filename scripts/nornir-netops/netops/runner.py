"""The Nornir tasks: detect the platform, read state, plan, optionally push."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Sequence

from nornir.core.task import Result, Task
from nornir_netmiko import netmiko_send_command, netmiko_send_config

from .rollback import default_reversal
from .debuglog import protect
from .core import (
    MODE_ADD,
    SAVE_COMMANDS,
    Feature,
    NotApplicable,
    canonical_platform,
    plan_changes,
    render,
    scrub,
)

# `show run | include ...` can take a while on a chassis with a large config,
# and `write memory` on a busy box longer still. netmiko's 10s default is too
# tight for both.
SHOW_TIMEOUT = 60
SAVE_TIMEOUT = 120

#: A device that does not understand a command answers with an error *string*,
#: not an error. Parsed as state, that reads as "nothing is configured" -- so
#: the tool would cheerfully configure everything, and a --replace would think
#: there was nothing to remove. Specific enough not to fire on a banner body
#: that happens to contain a percent sign.
CLI_ERRORS = (
    "% invalid input",
    "% incomplete command",
    "% ambiguous command",
    "% unknown command",
    "% authorization failed",
    "% permission denied",
    "invalid input detected",
)


def _check_understood(command: str, output: str) -> None:
    lowered = (output or "").lower()
    for marker in CLI_ERRORS:
        if marker in lowered:
            raise ValueError(
                f"the device did not accept {command!r}: "
                f"{output.strip().splitlines()[0][:120]}"
            )


def detect_platform(task: Task) -> Result:
    """Fill in a blank platform column by asking the device what it is.

    Runs before the main task so an unknown box fails during detection with a
    clear message instead of halfway through a config push. The answer is both
    the netmiko device_type used to connect and the template directory used to
    render, which is why it is canonicalized to netmiko's spelling.
    """
    from netmiko.ssh_autodetect import SSHDetect

    host = task.host
    params: Dict[str, Any] = {
        "device_type": "autodetect",
        "host": host.hostname,
        "username": host.username,
        "password": host.password,
        "port": host.port or 22,
    }
    extras = host.get_connection_parameters("netmiko").extras or {}
    if extras.get("secret"):
        params["secret"] = extras["secret"]
    # Autodetect opens its own session, so it needs the same patience limit --
    # otherwise an unreachable device stalls here on netmiko's default instead.
    for passthrough in ("conn_timeout", "use_keys", "key_file"):
        if extras.get(passthrough) is not None:
            params[passthrough] = extras[passthrough]

    guess = SSHDetect(**params).autodetect()
    if not guess:
        raise ValueError(
            "could not autodetect the platform; set the platform column in the CSV"
        )
    platform = canonical_platform(guess)
    host.platform = platform
    return Result(host=host, result=platform, changed=False)


def _read_state(task: Task, support) -> List:
    """Run every command this feature reads from, and parse the lot together."""
    output = []
    for command in support.commands:
        shown = task.run(
            task=netmiko_send_command,
            name=command,
            command_string=command,
            enable=True,
            read_timeout=SHOW_TIMEOUT,
        )
        _check_understood(command, shown.result or "")
        output.append(shown.result or "")
    return support.parse("\n".join(output))


def run_check(task: Task, check, expected, options) -> Result:
    """Read operational state and judge it. Changes nothing, ever."""
    platform = canonical_platform(task.host.platform)
    support = check.support_for(platform)  # raises UnsupportedPlatform

    output = []
    for command in support.commands:
        shown = task.run(
            task=netmiko_send_command,
            name=command,
            command_string=command,
            enable=True,
            read_timeout=SHOW_TIMEOUT,
        )
        _check_understood(command, shown.result or "")
        output.append(shown.result or "")

    state = support.parse("\n".join(output))
    verdict = check.evaluate(state, expected, options)
    return Result(
        host=task.host,
        result={
            "platform": platform,
            "status": verdict.status,
            "summary": verdict.summary,
            "reasons": verdict.reasons,
            "state": state,
        },
        changed=False,
    )


def apply_rollback(task: Task, save: bool) -> Result:
    """Send one device's recorded reversal."""
    commands = list(task.host.data.get("rollback") or [])
    payload: Dict[str, Any] = {
        "platform": canonical_platform(task.host.platform),
        "commands": commands,
        "applied": False,
        "saved": None,
        "output": None,
    }
    if not commands:
        return Result(host=task.host, result=payload, changed=False)

    pushed = task.run(
        task=netmiko_send_config, name="rollback", config_commands=commands
    )
    _check_understood("rollback", pushed.result or "")
    payload["applied"] = True
    payload["output"] = pushed.result

    save_command = SAVE_COMMANDS.get(payload["platform"]) if save else None
    if save_command:
        saved = task.run(
            task=netmiko_send_command,
            name=save_command,
            command_string=save_command,
            enable=True,
            read_timeout=SAVE_TIMEOUT,
        )
        _check_understood(save_command, saved.result or "")
        payload["saved"] = True
    return Result(host=task.host, result=payload, changed=True)


def configure_feature(
    task: Task,
    feature: Feature,
    desired: Sequence[str],
    variables: Mapping[str, Any],
    secrets: Sequence[str],
    mode: str,
    dry_run: bool,
    save: bool,
    verify: bool,
) -> Result:
    """Read current state, work out the delta, and apply it unless dry running.

    A dry run still connects: `--replace` cannot know what to remove without
    reading the device, and an add-only plan that ignored current state would
    report changes that are already in place.

    Nothing sensitive survives this function. Rendered commands and device
    output are scrubbed of `secrets` before they go into the payload, so the
    password reaches the device and neither the terminal nor the report.
    """
    if feature.run is not None:
        return feature.run(task, desired, variables, mode, dry_run, save, verify)

    platform = canonical_platform(task.host.platform)
    if platform in feature.platform_runs:
        return feature.platform_runs[platform](task, desired, variables, mode, dry_run, save, verify)
    payload: Dict[str, Any] = {
        "platform": platform,
        "mode": mode,
        "current": [],
        "desired": list(desired),
        "add": [],
        "remove": [],
        "commands": [],
        "save_command": None,
        "compliant": False,
        "advisories": [],
        "notes": [],
        "rollback": [],
        "rollback_unsupported": [],
        "applied": False,
        "saved": None,
        "skipped": False,
        "skip_reason": None,
        "verified": None,
        "missing_after": [],
        "output": None,
        "save_output": None,
    }

    audit_only = False
    policy_notes = []
    if feature.execution_policy is not None:
        mode, audit_only, policy = feature.execution_policy(task.host, mode, variables)
        payload.update(mode="audit" if audit_only else mode, audit_only=audit_only, policy=policy)
        policy_notes.append(f"policy: {policy}")
    if feature.audit_fields is not None:
        payload.update(checked_at=None, syslog_compliant=None)

    try:
        support = feature.support_for(platform)  # raises UnsupportedPlatform
    except NotApplicable as exc:
        # Not an error: the setting does not exist on this OS.
        payload.update(skipped=True, skip_reason=str(exc), compliant=True)
        return Result(host=task.host, result=payload, changed=False)

    # Each host gets its own copy, so a planner that records something for the
    # template -- which interfaces are missing what -- cannot race another host.
    variables = dict(variables)

    if feature.per_device is not None:
        # Before touching the device: an inventory that cannot say what this
        # device's source interface is should stop here, not halfway through.
        desired, variables = feature.per_device(list(desired), dict(variables), task.host)
    payload["desired"] = list(desired)

    current = _read_state(task, support)
    # A parser can flag a value it read off the device as sensitive -- an SNMP
    # community has to be named to be removed, and that name is a credential.
    secrets = list(secrets) + [
        entry.data["secret_value"]
        for entry in current
        if entry.data.get("secret_value")
    ]
    protect(secrets)
    context = {
        "login_user": task.host.username,
        "platform": platform,
        "variables": variables,
        "ignores": support.ignores,
        # A planner appends here when it finds drift it will not fix by itself.
        "advisories": [],
        # ...and here when it has something to say that is not a problem: how
        # many interfaces it looked at, say. Notes never affect compliance.
        "notes": policy_notes,
    }
    to_add, to_remove = feature.plan(current, desired, mode, context)
    advisories: List[str] = list(context["advisories"])
    notes: List[str] = list(context["notes"])

    commands: List[str] = []
    if to_add or to_remove:
        commands = render(
            feature.name, platform, to_add, to_remove, variables, feature.keep_blank_lines
        )

    payload.update(
        current=[entry.shown for entry in current],
        add=list(to_add),
        remove=[entry.shown for entry in to_remove],
        commands=[scrub(command, secrets) for command in commands],
        save_command=SAVE_COMMANDS.get(platform) if (commands and save and not audit_only) else None,
        advisories=advisories,
        notes=notes,
        # Drift we are not fixing is still drift: this device is not compliant.
        compliant=not commands and not advisories,
    )

    # Said before the change, when there is still time to take a backup.
    if commands and feature.rollback_note:
        payload["notes"].append(f"rollback: {feature.rollback_note}")
    if commands and not feature.reversible:
        payload["notes"].append("rollback: this change cannot be undone")

    if not commands or dry_run or audit_only:
        if feature.audit_fields is not None:
            payload.update(feature.audit_fields(current, desired, context))
            payload["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return Result(host=task.host, result=payload, changed=False)

    # Worked out now rather than later: once the device is changed it no longer
    # knows what it used to be.
    if feature.reversible:
        reverse_context = {
            "platform": platform,
            "variables": variables,
            "added": list(to_add),
            "secrets": list(secrets),
        }
        reversal = (
            feature.reverse(commands, current, to_remove, reverse_context)
            if feature.reverse is not None
            else default_reversal(commands, current, to_remove, secrets)
        )
        payload["rollback"] = reversal.commands
        payload["rollback_unsupported"] = reversal.unsupported

    try:
        pushed = task.run(
            task=netmiko_send_config,
            name=f"configure {feature.name}",
            config_commands=commands,
            **feature.config_options,
        )
        payload["applied"] = True
        payload["output"] = scrub(pushed.result, secrets)
        _check_understood(f"configure {feature.name}", payload["output"] or "")

        # Read back before saving. If a `no username x` landed but its replacement
        # did not, this is what notices -- and not saving leaves startup-config with
        # the account still in it.
        if verify:
            after = _read_state(task, support)
            if feature.verify_with_plan:
                # For a feature whose desired set is worked out from the device --
                # every access port, say -- "is anything still outstanding?" is the
                # only question worth asking, and its own planner is what answers it.
                verification_context = {**context, "advisories": [], "notes": []}
                again, remaining = feature.plan(after, desired, mode, verification_context)
                outstanding = list(again) + [f"remove {entry.key}" for entry in remaining]
                outstanding.extend(verification_context["advisories"])
                payload["verified"] = not outstanding
                payload["missing_after"] = outstanding
            else:
                missing, _ = plan_changes(after, desired, MODE_ADD)
                # A removal-only run must be verified too. Keys also being
                # rewritten (accounts, groups) are expected to remain present.
                removed_entries = {
                    (entry.key, entry.line) for entry in to_remove if entry.key not in to_add
                }
                remaining = sorted({
                    entry.key for entry in after if (entry.key, entry.line) in removed_entries
                })
                outstanding = missing + [f"remove {key}" for key in remaining]
                payload["verified"] = not outstanding
                payload["missing_after"] = outstanding

        if payload["save_command"] and payload["verified"] is not False:
            saved = task.run(
                task=netmiko_send_command,
                name=payload["save_command"],
                command_string=payload["save_command"],
                enable=True,
                read_timeout=SAVE_TIMEOUT,
            )
            payload["save_output"] = scrub(saved.result, secrets)
            _check_understood(payload["save_command"], payload["save_output"] or "")
            payload["saved"] = True
        elif payload["save_command"]:
            payload["saved"] = False

        if feature.audit_fields is not None and verify and payload["verified"]:
            payload.update(feature.audit_fields(after, desired, context))
            payload["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    except Exception as exc:
        # Preserve pre-change evidence even if the push, read-back or save
        # fails after the device has accepted some commands.
        return Result(host=task.host, result=payload, changed=True, failed=True, exception=exc)

    return Result(host=task.host, result=payload, changed=True)
