"""The Nornir tasks: detect the platform, read state, plan, optionally push."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from nornir.core.task import Result, Task
from nornir_netmiko import netmiko_send_command, netmiko_send_config

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
    platform = canonical_platform(task.host.platform)
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
        "applied": False,
        "saved": None,
        "skipped": False,
        "skip_reason": None,
        "verified": None,
        "missing_after": [],
        "output": None,
        "save_output": None,
    }

    try:
        support = feature.support_for(platform)  # raises UnsupportedPlatform
    except NotApplicable as exc:
        # Not an error: the setting does not exist on this OS.
        payload.update(skipped=True, skip_reason=str(exc), compliant=True)
        return Result(host=task.host, result=payload, changed=False)

    if feature.per_device is not None:
        # Before touching the device: an inventory that cannot say what this
        # device's source interface is should stop here, not halfway through.
        desired, variables = feature.per_device(list(desired), dict(variables), task.host)

    current = _read_state(task, support)
    # A parser can flag a value it read off the device as sensitive -- an SNMP
    # community has to be named to be removed, and that name is a credential.
    secrets = list(secrets) + [
        entry.data["secret_value"]
        for entry in current
        if entry.data.get("secret_value")
    ]
    context = {
        "login_user": task.host.username,
        "platform": platform,
        "variables": variables,
        "ignores": support.ignores,
        # A planner appends here when it finds drift it will not fix by itself.
        "advisories": [],
    }
    to_add, to_remove = feature.plan(current, desired, mode, context)
    advisories: List[str] = list(context["advisories"])

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
        save_command=SAVE_COMMANDS.get(platform) if (commands and save) else None,
        advisories=advisories,
        # Drift we are not fixing is still drift: this device is not compliant.
        compliant=not commands and not advisories,
    )

    if not commands or dry_run:
        return Result(host=task.host, result=payload, changed=False)

    pushed = task.run(
        task=netmiko_send_config,
        name=f"configure {feature.name}",
        config_commands=commands,
        **feature.config_options,
    )
    payload["applied"] = True
    payload["output"] = scrub(pushed.result, secrets)

    # Read back before saving. If a `no username x` landed but its replacement
    # did not, this is what notices -- and not saving leaves startup-config with
    # the account still in it.
    if verify:
        after = _read_state(task, support)
        missing, _ = plan_changes(after, desired, MODE_ADD)
        payload["verified"] = not missing
        payload["missing_after"] = missing

    if payload["save_command"] and payload["verified"] is not False:
        saved = task.run(
            task=netmiko_send_command,
            name=payload["save_command"],
            command_string=payload["save_command"],
            enable=True,
            read_timeout=SAVE_TIMEOUT,
        )
        payload["save_output"] = scrub(saved.result, secrets)
        payload["saved"] = True
    elif payload["save_command"]:
        payload["saved"] = False

    return Result(host=task.host, result=payload, changed=True)
