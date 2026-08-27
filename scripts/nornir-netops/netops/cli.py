"""Command line front end.

    configure.py ntp --servers 10.0.0.1,10.0.0.2            # dry run (default)
    configure.py ntp --servers 10.0.0.1,10.0.0.2 --apply    # add them
    configure.py ntp --servers 10.0.0.1 --replace --apply   # add them, remove the rest
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import MODE_ADD, MODE_REPLACE, Desired, Feature, scrub
from .credentials import (
    AwsSecretSpec,
    CredentialError,
    Credentials,
    find_env_file,
    load_env_file,
    resolve as resolve_credentials,
)
from .features import FEATURES

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DIFF = 2
EXIT_USAGE = 3


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #


class HelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    """Show defaults, but only the ones worth reading.

    argparse's own version appends "(default: None)" to every optional value
    and "(default: True)" to every --no-x flag, which buries the defaults that
    actually matter (the CSV path, the port, the AWS key names).
    """

    def _get_help_string(self, action: argparse.Action) -> Optional[str]:
        if action.nargs == 0 or action.default in (None, [], ""):
            return action.help
        return super()._get_help_string(action)


def bootstrap_env(argv: List[str]) -> Optional[str]:
    """Load the .env before the real parser is built.

    Doing it first means every ``[$VAR]`` default below -- inventory path, AWS
    secret name, port -- can come from the .env too, not just the credentials.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file")
    pre.add_argument("--no-env-file", action="store_true")
    known, _ = pre.parse_known_args(argv)
    if known.no_env_file:
        return None
    path = find_env_file(known.env_file, PROJECT_ROOT)
    if path is None:
        return None
    count = load_env_file(path)
    return f"{path} ({count} variable(s))"


def _common_arguments() -> argparse.ArgumentParser:
    """Options shared by every feature subcommand."""
    parent = argparse.ArgumentParser(add_help=False)

    inv = parent.add_argument_group("inventory")
    inv.add_argument(
        "-c",
        "--csv",
        default=os.environ.get("NETOPS_CSV", "inventory/hosts.csv"),
        help="CSV of devices [$NETOPS_CSV]",
    )
    inv.add_argument(
        "--limit", metavar="NAME[,NAME...]", help="only these devices, by name or address"
    )
    inv.add_argument(
        "--filter",
        action="append",
        default=[],
        metavar="COLUMN=VALUE",
        help="only devices whose CSV column matches (repeatable, ANDed)",
    )

    auth = parent.add_argument_group(
        "credentials",
        "Resolved per field: flag, then AWS secret, then environment/.env, then a "
        "prompt. A username/password column in the CSV overrides all of it for "
        "that device.",
    )
    auth.add_argument("-u", "--username", help="login user [$NET_USER]")
    auth.add_argument(
        "--password",
        help="login password [$NET_PASS] -- prefer the .env or AWS over this flag, "
        "which is visible in ps output",
    )
    auth.add_argument(
        "--secret", help="enable secret, if login does not land in privileged mode [$NET_ENABLE]"
    )
    auth.add_argument(
        "--key-file",
        default=os.environ.get("NET_KEY_FILE"),
        help="SSH private key to authenticate with instead of a password [$NET_KEY_FILE]",
    )
    auth.add_argument(
        "--port", type=int, default=int(os.environ.get("NET_PORT", "22")), help="SSH port"
    )
    auth.add_argument("--env-file", help="path to the .env (default: ./.env, then <project>/.env)")
    auth.add_argument("--no-env-file", action="store_true", help="ignore any .env file")

    aws = parent.add_argument_group(
        "aws secrets manager",
        "Read the login from a JSON secret using the ambient IAM identity -- an "
        "instance or task role needs no keys on disk.",
    )
    aws.add_argument(
        "--aws-secret",
        metavar="NAME_OR_ARN",
        default=os.environ.get("NET_AWS_SECRET"),
        help="secret to read; enables this source [$NET_AWS_SECRET]",
    )
    aws.add_argument(
        "--aws-region",
        default=os.environ.get("NET_AWS_REGION") or os.environ.get("AWS_REGION"),
        help="region of the secret [$NET_AWS_REGION, $AWS_REGION]",
    )
    aws.add_argument(
        "--aws-username-key",
        default=os.environ.get("NET_AWS_USERNAME_KEY", "username"),
        help="JSON key holding the username [$NET_AWS_USERNAME_KEY]",
    )
    aws.add_argument(
        "--aws-password-key",
        default=os.environ.get("NET_AWS_PASSWORD_KEY", "password"),
        help="JSON key holding the password [$NET_AWS_PASSWORD_KEY]",
    )
    aws.add_argument(
        "--aws-enable-key",
        default=os.environ.get("NET_AWS_ENABLE_KEY", "enable_secret"),
        help="JSON key holding the enable secret, if any [$NET_AWS_ENABLE_KEY]",
    )

    run = parent.add_argument_group("what to do")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument(
        "--add",
        dest="mode",
        action="store_const",
        const=MODE_ADD,
        default=MODE_ADD,
        help="add the desired entries, leave anything else alone (default)",
    )
    mode.add_argument(
        "--replace",
        dest="mode",
        action="store_const",
        const=MODE_REPLACE,
        default=MODE_ADD,
        help="add the desired entries and remove every other one",
    )
    run.add_argument(
        "--apply", action="store_true", help="actually push; without this nothing is changed"
    )
    run.add_argument(
        "--no-save",
        dest="save",
        action="store_false",
        default=True,
        help="do not write to startup-config after a successful change",
    )
    run.add_argument(
        "-y", "--yes", action="store_true", help="skip the confirmation prompt --apply asks for"
    )
    run.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        default=True,
        help="do not read the config back after applying to confirm the change landed",
    )

    out = parent.add_argument_group("output")
    out.add_argument("-w", "--workers", type=int, default=10, help="devices in parallel")
    out.add_argument("--report", metavar="FILE", help="write a JSON report of the run")
    out.add_argument(
        "--fail-on-diff",
        action="store_true",
        help=f"exit {EXIT_DIFF} if any device is out of compliance (for CI drift checks)",
    )
    out.add_argument(
        "-v", "--verbose", action="store_true", help="show current state and device output"
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="configure.py",
        description="Converge network device configuration from a CSV of addresses. "
        "Dry run unless --apply is given.",
    )
    common = _common_arguments()
    subs = parser.add_subparsers(dest="command", metavar="FEATURE", required=True)

    for feature in FEATURES.values():
        sub = subs.add_parser(
            feature.name,
            parents=[common],
            help=feature.help,
            description=feature.help,
            formatter_class=HelpFormatter,
        )
        feature.add_arguments(sub)
        sub.set_defaults(feature=feature)

    subs.add_parser(
        "selftest",
        help="render every template offline against sample output (touches no devices)",
        description="Renders each feature/platform template against sample device output. "
        "Run it after editing a template or adding a platform.",
    )
    return parser


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #


class Style:
    """Minimal ANSI, disabled when not writing to a terminal or under NO_COLOR."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def ok(self, t: str) -> str:
        return self(t, "32")

    def warn(self, t: str) -> str:
        return self(t, "33")

    def bad(self, t: str) -> str:
        return self(t, "31")

    def bold(self, t: str) -> str:
        return self(t, "1")

    def dim(self, t: str) -> str:
        return self(t, "2")


def _print_host(style: Style, name: str, record: Dict[str, Any], verbose: bool) -> None:
    header = f"{style.bold(name)} ({record.get('hostname', '')}) [{record.get('platform') or 'unknown'}]"
    status = record["status"]

    if status == "failed":
        print(f"{header} {style.bad('FAILED')}")
        for line in str(record["error"]).strip().splitlines():
            print(f"    {line}")
        return

    if status == "skipped":
        print(f"{header} {style.dim('skipped')} -- {record['skip_reason']}")
        return

    if status == "ok":
        print(f"{header} {style.ok('already compliant')}")
        if verbose:
            for line in record["current"]:
                print(style.dim(f"    {line}"))
        return

    if status == "unverified":
        label = style.bad("APPLIED BUT NOT VERIFIED")
    elif status == "changed":
        label = style.ok("applied")
    else:
        label = style.warn("would run")
    count = len(record["commands"]) + (1 if record["save_command"] else 0)
    print(f"{header} {label} ({count} command{'s' if count != 1 else ''})")

    if verbose and record["current"]:
        print(style.dim("    current:"))
        for line in record["current"]:
            print(style.dim(f"      {line}"))
    for command in record["commands"]:
        print(f"    {command}")
    if record["save_command"]:
        print(f"    {record['save_command']}")
    if status == "unverified":
        missing = ", ".join(record["missing_after"])
        print(style.bad(f"    !! still missing after the change: {missing}"))
        print(style.bad("    !! startup-config was NOT saved; check this device now"))
    if verbose and record.get("output"):
        print(style.dim("    device output:"))
        for line in str(record["output"]).strip().splitlines():
            print(style.dim(f"      {line}"))


def _print_report(
    style: Style, records: Dict[str, Dict[str, Any]], dry_run: bool, verbose: bool
) -> None:
    print()
    for name in sorted(records):
        _print_host(style, name, records[name], verbose)
        print()

    counts = {"ok": 0, "pending": 0, "changed": 0, "failed": 0, "skipped": 0, "unverified": 0}
    for record in records.values():
        counts[record["status"]] += 1

    summary = [f"{len(records)} device(s)", f"{counts['ok']} compliant"]
    if dry_run:
        summary.append(style.warn(f"{counts['pending']} with pending changes"))
    else:
        summary.append(style.ok(f"{counts['changed']} changed"))
    if counts["skipped"]:
        summary.append(style.dim(f"{counts['skipped']} not applicable"))
    if counts["unverified"]:
        summary.append(style.bad(f"{counts['unverified']} unverified"))
    if counts["failed"]:
        summary.append(style.bad(f"{counts['failed']} failed"))
    print(style.bold("summary: ") + ", ".join(summary))
    if dry_run and counts["pending"]:
        print(style.dim("re-run with --apply to push the commands above"))


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #


def selftest() -> int:
    """Render every template offline. No nornir, no network, no credentials."""
    from .core import render

    failures = 0
    for feature in FEATURES.values():
        sub = argparse.ArgumentParser(prog=f"selftest {feature.name}")
        feature.add_arguments(sub)
        # A feature whose values come from the environment (passwords) declares
        # placeholders here rather than putting them on a command line.
        for key, value in feature.selftest_env.items():
            os.environ.setdefault(key, value)
        desired = feature.build_desired(sub.parse_args(feature.selftest_args))
        print(f"### {feature.name}  {' '.join(feature.selftest_args)}")
        print()

        for platform, reason in sorted(feature.not_applicable.items()):
            print(f"--- {platform} --- not applicable: {reason}")
            print()

        for platform, support in sorted(feature.platforms.items()):
            current = support.parse(support.sample)
            print(f"--- {platform} ---")
            print(f"  {support.show_command}")
            for entry in current:
                print(f"    {entry.shown}")
            print(
                f"  parsed: {', '.join(e.key for e in current) or '(nothing)'}"
            )
            for mode in (MODE_ADD, MODE_REPLACE):
                # the feature's own planner, so a rotation or a scalar setting
                # renders here exactly as it would against a device
                add, remove = feature.plan(
                    current,
                    desired.keys,
                    mode,
                    {
                        "login_user": None,
                        "platform": platform,
                        "variables": desired.variables,
                    },
                )
                try:
                    commands = render(
                        feature.name, platform, add, remove, desired.variables
                    )
                except Exception as exc:  # a template bug: report it, keep testing
                    failures += 1
                    print(f"  --{mode}: RENDER FAILED: {exc}")
                    continue
                print(f"  --{mode}:")
                for command in commands:
                    print(f"    {scrub(command, desired.secrets)}")
                if not commands:
                    print("    (no changes)")
            print()

    if failures:
        print(f"{failures} template(s) failed to render")
        return EXIT_FAILED
    print("all templates rendered")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _apply_filters(nr, args: argparse.Namespace):
    targets = nr
    if args.limit:
        wanted = {v.strip() for v in args.limit.split(",") if v.strip()}
        targets = targets.filter(filter_func=lambda h, w=wanted: h.name in w or h.hostname in w)
    for expression in args.filter:
        column, separator, value = expression.partition("=")
        if not separator:
            raise ValueError(f"--filter needs COLUMN=VALUE, got {expression!r}")
        targets = targets.filter(
            filter_func=lambda h, c=column.strip().lower(), v=value.strip(): str(
                h.data.get(c, "")
            )
            == v
        )
    return targets


def _confirm(style: Style, count: int, feature: str, mode: str) -> bool:
    if not sys.stdin.isatty():
        return True  # non-interactive: --apply was explicit enough
    verb = "add to" if mode == MODE_ADD else "replace on"
    answer = input(
        style.warn(f"About to {verb} {feature} on {count} device(s). Continue? [y/N] ")
    )
    return answer.strip().lower() in {"y", "yes"}


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    style = Style(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))

    try:
        env_note = bootstrap_env(argv)
    except CredentialError as exc:
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()

    feature: Feature = args.feature
    try:
        desired: Desired = feature.build_desired(args)
    except CredentialError as exc:
        # A password the feature needed could not be found or read -- same
        # class of problem as a missing device login, so same exit code.
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:
        parser.error(str(exc))

    aws = (
        AwsSecretSpec(
            name=args.aws_secret,
            region=args.aws_region,
            username_key=args.aws_username_key,
            password_key=args.aws_password_key,
            enable_key=args.aws_enable_key,
        )
        if args.aws_secret
        else None
    )
    try:
        credentials: Credentials = resolve_credentials(
            username=args.username,
            password=args.password,
            secret=args.secret,
            aws=aws,
            prompt=sys.stdin.isatty(),
            key_file=args.key_file,
        )
    except CredentialError as exc:
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE

    # nornir is imported here so `selftest` and --help work without it installed.
    from .inventory import InventoryError, init_nornir, missing_credentials
    from .runner import configure_feature, detect_platform

    try:
        nr = init_nornir(
            csv_file=args.csv,
            username=credentials.username,
            password=credentials.password,
            secret=credentials.secret,
            key_file=args.key_file,
            port=args.port,
            workers=args.workers,
        )
        targets = _apply_filters(nr, args)
    except (InventoryError, ValueError) as exc:
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE

    if not targets.inventory.hosts:
        print(style.bad("error: no devices matched --limit/--filter"), file=sys.stderr)
        return EXIT_USAGE

    incomplete = missing_credentials(targets, key_file=args.key_file)
    if incomplete:
        shown = ", ".join(incomplete[:10]) + ("..." if len(incomplete) > 10 else "")
        print(
            style.bad(f"error: no credentials for: {shown}"),
            file=sys.stderr,
        )
        print(
            "set them in the .env (NET_USER/NET_PASS), in AWS Secrets Manager "
            "(--aws-secret), or per device in the CSV",
            file=sys.stderr,
        )
        return EXIT_USAGE

    dry_run = not args.apply
    count = len(targets.inventory.hosts)
    banner = (
        style.warn("DRY RUN -- no configuration will be changed")
        if dry_run
        else style.bad("APPLYING CHANGES")
    )
    print(f"{banner}  |  {feature.name}, mode={args.mode}, {count} device(s)")
    if env_note:
        print(style.dim(f"env file: {env_note}"))
    print(style.dim(f"credentials: {credentials.describe()}"))

    if not dry_run and not args.yes and not _confirm(style, count, feature.name, args.mode):
        print("aborted")
        return EXIT_USAGE

    records: Dict[str, Dict[str, Any]] = {
        name: {
            "hostname": host.hostname,
            "platform": host.platform,
            "status": "failed",
            "error": "did not run",
            "current": [],
            "commands": [],
            "save_command": None,
        }
        for name, host in targets.inventory.hosts.items()
    }

    # Devices with a blank platform column get one detection pass up front, so a
    # box we cannot identify fails before anything is pushed anywhere.
    unknown = targets.filter(filter_func=lambda h: not h.platform)
    if unknown.inventory.hosts:
        print(f"detecting platform on {len(unknown.inventory.hosts)} device(s)...")
        for name, result in unknown.run(task=detect_platform).items():
            if result.failed:
                records[name]["error"] = f"platform detection failed: {_error_of(result)}"
            else:
                records[name]["platform"] = result.result

    ready = targets.filter(filter_func=lambda h: bool(h.platform))
    if ready.inventory.hosts:
        results = ready.run(
            task=configure_feature,
            feature=feature,
            desired=desired.keys,
            variables=desired.variables,
            secrets=desired.secrets,
            mode=args.mode,
            dry_run=dry_run,
            save=args.save,
            verify=args.verify,
        )
        for name, result in results.items():
            if result.failed:
                records[name]["error"] = _error_of(result)
                continue
            payload = result[0].result
            payload["hostname"] = targets.inventory.hosts[name].hostname
            payload["error"] = None
            payload["status"] = _status_of(payload)
            records[name] = payload

    _print_report(style, records, dry_run, args.verbose)

    if args.report:
        _write_report(args, feature, desired, dry_run, records)
        print(f"report written to {args.report}")

    if any(r["status"] in ("failed", "unverified") for r in records.values()):
        return EXIT_FAILED
    if args.fail_on_diff and any(r["status"] == "pending" for r in records.values()):
        return EXIT_DIFF
    return EXIT_OK


def _status_of(payload: Dict[str, Any]) -> str:
    if payload["skipped"]:
        return "skipped"
    if payload["verified"] is False:
        return "unverified"  # the change was pushed but did not take
    if payload["compliant"]:
        return "ok"
    return "changed" if payload["applied"] else "pending"


def _error_of(result) -> str:
    """The useful part of a failed MultiResult: the deepest exception."""
    for item in reversed(list(result)):
        if item.exception is not None:
            return f"{type(item.exception).__name__}: {item.exception}"
    return "unknown error"


def _write_report(
    args: argparse.Namespace,
    feature: Feature,
    desired: Desired,
    dry_run: bool,
    records: Dict[str, Dict[str, Any]],
) -> None:
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feature": feature.name,
        "mode": args.mode,
        "dry_run": dry_run,
        "desired": desired.keys,
        "variables": desired.variables,
        "devices": records,
    }
    # Serialize then scrub, covering both the raw secret and its JSON-escaped
    # form -- a password containing a quote or a backslash is written escaped.
    text = json.dumps(document, indent=2, sort_keys=True, default=str)
    escaped = [json.dumps(secret)[1:-1] for secret in desired.secrets]
    text = scrub(text, list(desired.secrets) + escaped)
    with open(args.report, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
