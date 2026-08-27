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
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .core import MODE_ADD, MODE_REPLACE, Desired, Feature, scrub
from .debuglog import DEFAULT_LOG_FILE, DebugLog, configure as configure_log
from .platform_cache import DEFAULT_FILENAME as DEFAULT_CACHE_FILE
from .platform_cache import DEFAULT_TTL_HOURS
from .platform_cache import load as load_platform_cache
from .errors import summarize
from .credentials import (
    AwsSecretSpec,
    CredentialError,
    Credentials,
    find_env_file,
    load_env_file,
    resolve as resolve_credentials,
)
from .features import FEATURES
from .standards import Standards, StandardsError, load as load_standards
from . import servicenow

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DIFF = 2
EXIT_USAGE = 3
EXIT_INTERRUPTED = 130  # what a shell expects from Ctrl-C

#: Report order: the quiet devices first, the ones needing attention last, so
#: what matters is next to the summary rather than scrolled off the top.
STATUS_ORDER = {
    "ok": 0,
    "skipped": 1,
    "pending": 2,
    "changed": 3,
    "attention": 4,
    "unverified": 5,
    "failed": 6,
}


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


def bootstrap_log(argv: List[str]) -> DebugLog:
    """Open the debug log before the real parser runs.

    Same trick as the .env: doing it first means a crash in argument parsing or
    inventory loading is logged too, not just a device failure.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--log-file", default=os.environ.get("NETOPS_LOG_FILE", DEFAULT_LOG_FILE))
    pre.add_argument("--no-log-file", action="store_true")
    pre.add_argument("--debug", action="store_true")
    known, _ = pre.parse_known_args(argv)
    return configure_log(None if known.no_log_file else known.log_file, known.debug)


def _connection_arguments(parent: argparse.ArgumentParser) -> None:
    """How to reach the devices. Shared by the features and by `discover`."""
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
    cache = parent.add_argument_group(
        "platform cache",
        "Autodetecting a platform costs an extra SSH login per device, and the "
        "answer almost never changes, so it is remembered. A platform column in "
        "the CSV always wins over the cache.",
    )
    cache.add_argument(
        "--platform-cache",
        metavar="FILE",
        help=f"where detected platforms are remembered "
        f"(default: <project>/{DEFAULT_CACHE_FILE}) [$NETOPS_PLATFORM_CACHE]",
    )
    cache.add_argument(
        "--platform-cache-ttl",
        type=float,
        default=float(os.environ.get("NETOPS_PLATFORM_CACHE_TTL", DEFAULT_TTL_HOURS)),
        metavar="HOURS",
        help="how long a remembered platform stays good",
    )
    cache.add_argument(
        "--no-platform-cache",
        action="store_true",
        help="ignore what was remembered and detect again",
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
    auth.add_argument(
        "--conn-timeout",
        type=float,
        default=float(os.environ.get("NET_CONN_TIMEOUT", "10")),
        metavar="SECONDS",
        help="how long to wait for the TCP connection before giving up on a device",
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

    out = parent.add_argument_group("output")
    out.add_argument("-w", "--workers", type=int, default=10, help="devices in parallel")
    out.add_argument(
        "-v", "--verbose", action="store_true", help="show current state and device output"
    )
    out.add_argument(
        "--log-file",
        default=os.environ.get("NETOPS_LOG_FILE", DEFAULT_LOG_FILE),
        metavar="FILE",
        help="where full errors and tracebacks are written [$NETOPS_LOG_FILE]",
    )
    out.add_argument("--no-log-file", action="store_true", help="do not write a debug log")
    out.add_argument(
        "--debug",
        action="store_true",
        help="print full tracebacks, and log the SSH transcript to the log file",
    )


def _common_arguments() -> argparse.ArgumentParser:
    """Options shared by every feature subcommand."""
    parent = argparse.ArgumentParser(add_help=False)
    _connection_arguments(parent)

    desired = parent.add_argument_group("desired state")
    desired.add_argument(
        "--standards",
        metavar="FILE",
        default=os.environ.get("NETOPS_STANDARDS"),
        help="the desired state file (default: ./standards.yaml, then "
        "<project>/standards.yaml) [$NETOPS_STANDARDS]",
    )
    desired.add_argument(
        "--no-standards",
        action="store_true",
        help="ignore the standards file; take everything from flags",
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

    report = parent.add_argument_group("reporting")
    report.add_argument("--report", metavar="FILE", help="write a JSON report of the run")
    report.add_argument(
        "--fail-on-diff",
        action="store_true",
        help=f"exit {EXIT_DIFF} if any device is out of compliance (for CI drift checks)",
    )

    snow = parent.add_argument_group(
        "servicenow",
        "Open a change from a dry run, and close one after applying. The tool "
        "never approves a change: one that has not reached Scheduled or "
        "Implement is refused before any device is touched.",
    )
    snow.add_argument(
        "--open-change",
        action="store_true",
        help="create a Normal change from this dry run -- the plan, the device "
        "list and the report -- and print its number. Changes nothing on any device.",
    )
    snow.add_argument(
        "--change",
        metavar="CHG0012345",
        help="implement against this change: verify it is approved, apply, then "
        "add work notes, attach the report and close it",
    )
    snow.add_argument(
        "--snow-instance",
        metavar="NAME_OR_URL",
        help="ServiceNow instance [$SNOW_INSTANCE, or change.instance in the "
        "standards file]",
    )
    snow.add_argument(
        "--snow-secret",
        metavar="NAME_OR_ARN",
        default=os.environ.get("SNOW_SECRET"),
        help="AWS secret holding the ServiceNow credentials [$SNOW_SECRET]",
    )
    return parent


def _discover_arguments() -> argparse.ArgumentParser:
    """`discover` needs to reach the devices and nothing else -- no desired
    state, no change flags, because it changes nothing."""
    parent = argparse.ArgumentParser(add_help=False)
    _connection_arguments(parent)
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

    found = subs.add_parser(
        "discover",
        parents=[_discover_arguments()],
        help="detect each device's platform and remember it; changes nothing",
        description="Connect to every device whose CSV platform column is blank, "
        "work out what it is, and write that to the platform cache so later runs "
        "do not have to. Reads nothing but the login banner and changes nothing.",
        formatter_class=HelpFormatter,
    )
    found.add_argument(
        "--refresh",
        action="store_true",
        help="detect again even for devices already remembered",
    )

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
        print(f"{header} {style.bad('FAILED')} -- {record['error']}")
        for line in str(record.get("traceback") or "").rstrip().splitlines():
            print(style.dim(f"    {line}"))
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

    if status == "attention":
        print(f"{header} {style.warn('NEEDS ATTENTION')}")
        for note in record["advisories"]:
            print(style.warn(f"    {note}"))
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
    for note in record.get("advisories") or ():
        print(style.warn(f"    {note}"))
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
    for name in sorted(records, key=lambda n: (STATUS_ORDER[records[n]["status"]], n)):
        _print_host(style, name, records[name], verbose)
        print()

    counts = {
        "ok": 0,
        "pending": 0,
        "changed": 0,
        "failed": 0,
        "skipped": 0,
        "unverified": 0,
        "attention": 0,
    }
    for record in records.values():
        counts[record["status"]] += 1

    summary = [f"{len(records)} device(s)", f"{counts['ok']} compliant"]
    if dry_run:
        summary.append(style.warn(f"{counts['pending']} with pending changes"))
    else:
        summary.append(style.ok(f"{counts['changed']} changed"))
    if counts["skipped"]:
        summary.append(style.dim(f"{counts['skipped']} not applicable"))
    if counts["attention"]:
        summary.append(style.warn(f"{counts['attention']} needing attention"))
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

    # Rendering against the real standards file makes this a check of the file
    # too: a value the templates cannot render fails here rather than on a
    # device.
    try:
        standards = load_standards(None, PROJECT_ROOT, allow_example=True)
    except StandardsError as exc:
        print(f"standards file: {exc}")
        return EXIT_FAILED
    if standards.loaded:
        print(f"standards: {standards.path}\n")
    for warning in standards.warnings:
        print(f"warning: {warning}")

    failures = 0
    for feature in FEATURES.values():
        sub = argparse.ArgumentParser(prog=f"selftest {feature.name}")
        feature.add_arguments(sub)
        # A feature whose values come from the environment (passwords) declares
        # placeholders here rather than putting them on a command line.
        placeholders = dict(feature.selftest_env)
        if feature.selftest_env_from is not None:
            placeholders.update(feature.selftest_env_from(standards))
        for key, value in placeholders.items():
            os.environ.setdefault(key, value)
        namespace = sub.parse_args(feature.selftest_args)
        namespace.standards = standards
        desired = feature.build_desired(namespace)
        print(f"### {feature.name}  {' '.join(feature.selftest_args)}")
        print()

        for platform, reason in sorted(feature.not_applicable.items()):
            print(f"--- {platform} --- not applicable: {reason}")
            print()

        for platform, support in sorted(feature.platforms.items()):
            current = support.parse(support.sample)
            # Same rule as a real run: a value the parser read off the device
            # and flagged as sensitive is scrubbed from what gets printed.
            shown_secrets = list(desired.secrets) + [
                entry.data["secret_value"]
                for entry in current
                if entry.data.get("secret_value")
            ]
            print(f"--- {platform} ---")
            for command in support.commands:
                print(f"  {command}")
            for entry in current:
                print(f"    {entry.shown}")
            print(
                f"  parsed: {', '.join(e.key for e in current) or '(nothing)'}"
            )
            for mode in (MODE_ADD, MODE_REPLACE):
                advisories: List[str] = []
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
                        "ignores": support.ignores,
                        "advisories": advisories,
                    },
                )
                try:
                    commands = render(
                        feature.name,
                        platform,
                        add,
                        remove,
                        desired.variables,
                        feature.keep_blank_lines,
                    )
                except Exception as exc:  # a template bug: report it, keep testing
                    failures += 1
                    print(f"  --{mode}: RENDER FAILED: {exc}")
                    continue
                print(f"  --{mode}:")
                for command in commands:
                    print(f"    {scrub(command, shown_secrets)}")
                for note in advisories:
                    print(f"    ! {note}")
                if not commands and not advisories:
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
    """Thin wrapper: however a run ends, the terminal gets one line about it.

    A traceback is a debugging aid for whoever wrote this, not for whoever is
    pushing config to 200 switches. It goes to the log; the operator gets a
    sentence and a path.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    style = Style(sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
    log = bootstrap_log(argv)
    try:
        return _run(argv, style, log)
    except KeyboardInterrupt:
        print(style.warn("interrupted -- anything already applied is above"), file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 - the whole point is to not leak it
        summary = summarize(exc)
        print(style.bad(f"error: {summary}"), file=sys.stderr)
        log.failure("run", summary, exc)
        if log.debug or log.logger is None:
            traceback.print_exc()  # asked for it, or nowhere else to put it
        return EXIT_FAILED
    finally:
        if log.used and log.path:
            print(style.dim(f"full detail in {log.path}"))


def _run(argv: List[str], style: Style, log: DebugLog) -> int:
    try:
        env_note = bootstrap_env(argv)
    except CredentialError as exc:
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()
    if args.command == "discover":
        return _discover(args, style, log)

    feature: Feature = args.feature
    try:
        args.standards = (
            Standards() if args.no_standards else load_standards(args.standards, PROJECT_ROOT)
        )
    except StandardsError as exc:
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE
    for warning in args.standards.warnings:
        print(style.warn(f"warning: {warning}"), file=sys.stderr)

    try:
        desired: Desired = feature.build_desired(args)
    except StandardsError as exc:
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE
    except CredentialError as exc:
        # A password the feature needed could not be found or read -- same
        # class of problem as a missing device login, so same exit code.
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:
        if not args.standards.loaded and "standards file" in str(exc):
            parser.error(
                f"{exc} -- there is no standards file here; copy "
                f"{PROJECT_ROOT / 'standards.yaml.example'} to standards.yaml"
            )
        parser.error(str(exc))

    targets, credentials, code = _connect(args, style)
    if targets is None:
        return code

    dry_run = not args.apply

    if args.open_change and args.change:
        print(
            style.bad("error: --open-change opens a new change; --change implements "
                      "an existing one. Use one or the other."),
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.open_change and args.apply:
        print(
            style.bad("error: --open-change is a dry-run action -- it records what "
                      "would be done so somebody can approve it. Drop --apply."),
            file=sys.stderr,
        )
        return EXIT_USAGE

    snow: Optional[servicenow.Client] = None
    change: Dict[str, Any] = {}
    if args.open_change or args.change:
        try:
            snow = _servicenow_client(args, style)
            if args.change:
                # Before anything is pushed: is this change actually approved?
                change = snow.get_change(args.change)
                servicenow.ensure_implementable(change, snow.settings)
        except servicenow.ChangeNotApprovedError as exc:
            print(style.bad(f"error: {exc}"), file=sys.stderr)
            return EXIT_USAGE
        except servicenow.ServiceNowError as exc:
            print(style.bad(f"error: ServiceNow: {exc}"), file=sys.stderr)
            log.failure("servicenow", str(exc), exc)
            return EXIT_USAGE

    count = len(targets.inventory.hosts)
    banner = (
        style.warn("DRY RUN -- no configuration will be changed")
        if dry_run
        else style.bad("APPLYING CHANGES")
    )
    print(
        f"{banner}  |  {feature.name}, mode={args.mode}, {count} device(s), "
        f"{min(args.workers, count)} at a time"
    )
    if change:
        print(
            style.dim(
                f"change: {change.get('number')} "
                f"({snow.settings.state_name(change.get('state'))})"
            )
        )
    log.note(
        "run: feature=%s mode=%s devices=%d workers=%d apply=%s",
        feature.name, args.mode, count, args.workers, args.apply,
    )
    if env_note:
        print(style.dim(f"env file: {env_note}"))
    if args.standards.loaded:
        print(style.dim(f"standards: {args.standards.path}"))
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
    # box we cannot identify fails before anything is pushed anywhere. What was
    # detected last time is reused first: it is an extra login per device, and
    # the answer almost never changes.
    from .runner import detect_platform

    cache = load_platform_cache(
        args.platform_cache,
        PROJECT_ROOT,
        args.platform_cache_ttl,
        enabled=not args.no_platform_cache,
    )
    for host in targets.inventory.hosts.values():
        if host.platform:
            continue
        remembered = cache.get(host)
        if remembered:
            host.platform = remembered
            records[host.name]["platform"] = remembered
    if cache.hits:
        print(style.dim(f"platform: {cache.hits} remembered, {cache.path}"))

    unknown = targets.filter(filter_func=lambda h: not h.platform)
    if unknown.inventory.hosts:
        print(f"detecting platform on {len(unknown.inventory.hosts)} device(s)...")
        for name, result in unknown.run(task=detect_platform).items():
            if result.failed:
                _record_failure(
                    records, name, result, log, args.debug, "platform detection failed: "
                )
            else:
                records[name]["platform"] = result.result
                cache.put(targets.inventory.hosts[name], result.result)
        cache.save()

    from .runner import configure_feature

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
                _record_failure(records, name, result, log, args.debug)
                continue
            payload = result[0].result
            payload["hostname"] = targets.inventory.hosts[name].hostname
            payload["error"] = None
            payload["status"] = _status_of(payload)
            records[name] = payload

    _print_report(style, records, dry_run, args.verbose)

    number = change.get("number") if change else None
    report = _report_text(args, feature, desired, dry_run, records, number)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report)
        print(f"report written to {args.report}")

    snow_failed = False
    if snow is not None:
        try:
            number = _record_change(snow, args, feature, records, report, change, style)
        except servicenow.ServiceNowError as exc:
            snow_failed = True
            print(style.bad(f"ServiceNow: {exc}"), file=sys.stderr)
            log.failure("servicenow", str(exc), exc)
            if args.change:
                print(
                    style.bad(
                        f"the devices are done, but {args.change} was not closed -- "
                        f"close it by hand"
                    ),
                    file=sys.stderr,
                )

    if snow_failed:
        return EXIT_FAILED
    if any(r["status"] in ("failed", "unverified") for r in records.values()):
        return EXIT_FAILED
    if any(r["status"] == "attention" for r in records.values()):
        # Drift the tool declined to fix always needs a human, whether or not
        # --fail-on-diff was asked for.
        return EXIT_DIFF
    if args.fail_on_diff and any(r["status"] == "pending" for r in records.values()):
        return EXIT_DIFF
    return EXIT_OK


def _discover(args: argparse.Namespace, style: Style, log: DebugLog) -> int:
    """Work out what each device is and remember it, changing nothing."""
    targets, _, code = _connect(args, style)
    if targets is None:
        return code

    from .runner import detect_platform

    cache = load_platform_cache(
        args.platform_cache,
        PROJECT_ROOT,
        args.platform_cache_ttl,
        enabled=not args.no_platform_cache,
    )

    stated: List[Tuple[str, str]] = []
    remembered: List[Tuple[str, str]] = []
    pending: List[str] = []
    for name, host in sorted(targets.inventory.hosts.items()):
        if host.platform:
            stated.append((name, host.platform))  # the CSV said so; never detect
            continue
        known = None if args.refresh else cache.get(host)
        if known:
            remembered.append((name, known))
        else:
            pending.append(name)

    detected: List[Tuple[str, str]] = []
    failed: List[Tuple[str, str]] = []
    if pending:
        wanted = set(pending)
        print(
            f"detecting platform on {len(pending)} device(s), "
            f"{min(args.workers, len(pending))} at a time..."
        )
        todo = targets.filter(filter_func=lambda h, w=wanted: h.name in w)
        for name, result in todo.run(task=detect_platform).items():
            if result.failed:
                exception = _exception_of(result)
                message = summarize(exception) if exception else "unknown error"
                failed.append((name, message))
                log.failure(name, message, exception)
            else:
                detected.append((name, result.result))
                cache.put(targets.inventory.hosts[name], result.result)
        cache.save()

    width = max((len(name) for name, _ in stated + remembered + detected + failed), default=0)
    print()
    for name, platform in sorted(stated):
        print(f"  {name:<{width}}  {platform:<14} {style.dim('from the CSV')}")
    for name, platform in sorted(remembered):
        print(f"  {name:<{width}}  {platform:<14} {style.dim('remembered')}")
    for name, platform in sorted(detected):
        print(f"  {name:<{width}}  {style.ok(platform.ljust(14))} detected")
    for name, message in sorted(failed):
        print(f"  {name:<{width}}  {style.bad('unknown'.ljust(14))} {message}")

    summary = [f"{len(targets.inventory.hosts)} device(s)"]
    if stated:
        summary.append(f"{len(stated)} from the CSV")
    if remembered:
        summary.append(f"{len(remembered)} remembered")
    if detected:
        summary.append(style.ok(f"{len(detected)} detected"))
    if failed:
        summary.append(style.bad(f"{len(failed)} unidentified"))
    print()
    print(style.bold("summary: ") + ", ".join(summary))
    if cache.writes and cache.path:
        print(style.dim(f"remembered in {cache.path} for {args.platform_cache_ttl:g}h"))

    return EXIT_FAILED if failed else EXIT_OK


def _connect(args: argparse.Namespace, style: Style):
    """Resolve credentials, read the CSV, apply the filters.

    Shared by a feature run and by `discover`, which need exactly the same
    setup and differ only in what they do once connected.
    """
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
        return None, None, EXIT_USAGE

    # nornir is imported here so `selftest` and --help work without it installed.
    from .inventory import InventoryError, init_nornir, missing_credentials

    try:
        nr = init_nornir(
            csv_file=args.csv,
            username=credentials.username,
            password=credentials.password,
            secret=credentials.secret,
            key_file=args.key_file,
            port=args.port,
            workers=args.workers,
            conn_timeout=args.conn_timeout,
        )
        targets = _apply_filters(nr, args)
    except (InventoryError, ValueError) as exc:
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return None, None, EXIT_USAGE

    if not targets.inventory.hosts:
        print(style.bad("error: no devices matched --limit/--filter"), file=sys.stderr)
        return None, None, EXIT_USAGE

    incomplete = missing_credentials(targets, key_file=args.key_file)
    if incomplete:
        shown = ", ".join(incomplete[:10]) + ("..." if len(incomplete) > 10 else "")
        print(style.bad(f"error: no credentials for: {shown}"), file=sys.stderr)
        print(
            "set them in the .env (NET_USER/NET_PASS), in AWS Secrets Manager "
            "(--aws-secret), or per device in the CSV",
            file=sys.stderr,
        )
        return None, None, EXIT_USAGE

    return targets, credentials, EXIT_OK


def _servicenow_client(args: argparse.Namespace, style: Style) -> servicenow.Client:
    settings = servicenow.settings_from(args.standards, args)
    if args.snow_secret:
        servicenow.load_secret(args.snow_secret, args.aws_region, settings)
    return servicenow.Client(settings)


def _record_change(
    snow: servicenow.Client,
    args: argparse.Namespace,
    feature: Feature,
    records: Dict[str, Dict[str, Any]],
    report: str,
    change: Dict[str, Any],
    style: Style,
) -> Optional[str]:
    """Open a change from a dry run, or close the one we just implemented."""
    counts: Dict[str, int] = {}
    for record in records.values():
        counts[record["status"]] = counts.get(record["status"], 0) + 1
    plan = servicenow.describe_plan(feature.name, args.mode, records)

    if args.open_change:
        pending = counts.get("pending", 0)
        created = snow.create_change(
            {
                "short_description": (
                    f"netops: converge {feature.name} on {pending} device(s)"
                ),
                "description": plan,
                "implementation_plan": plan,
                # Not a backout procedure -- an honest statement of what exists.
                "backout_plan": (
                    "The configuration read from each device before the change is "
                    "recorded in the attached report, under devices.*.current."
                ),
                "test_plan": (
                    f"Re-run `configure.py {feature.name} --fail-on-diff`. Exit 0 "
                    f"means every device matches the standard."
                ),
                "state": snow.settings.state("new"),
                **snow.settings.fields,
            }
        )
        number = created.get("number")
        snow.attach(
            created["sys_id"],
            f"netops-{feature.name}-plan.json",
            report.encode("utf-8"),
            "application/json",
        )
        print(style.ok(f"opened {number} in {snow.settings.state_name(created.get('state'))}"))
        print(
            style.dim(
                f"approve it, then: configure.py {feature.name} --apply --change {number}"
            )
        )
        return number

    # Implementing: note what happened, attach the evidence, close it.
    sys_id = change["sys_id"]
    close_code, reason = servicenow.close_code_for(counts)
    snow.add_work_note(sys_id, servicenow.summarize_outcome(records))
    snow.attach(
        sys_id,
        f"netops-{feature.name}-result.json",
        report.encode("utf-8"),
        "application/json",
    )
    snow.update_change(
        sys_id,
        {
            "state": snow.settings.state("closed"),
            "close_code": close_code,
            "close_notes": f"netops {feature.name}: {reason}.",
        },
    )
    print(style.ok(f"closed {change.get('number')} as {close_code} ({reason})"))
    return change.get("number")


def _status_of(payload: Dict[str, Any]) -> str:
    if payload["skipped"]:
        return "skipped"
    if payload["verified"] is False:
        return "unverified"  # the change was pushed but did not take
    if payload["compliant"]:
        return "ok"
    if payload.get("advisories") and not payload["commands"]:
        return "attention"  # out of compliance, and not ours to fix
    return "changed" if payload["applied"] else "pending"


def _exception_of(result) -> Optional[BaseException]:
    """The deepest exception in a failed MultiResult -- the one that actually
    went wrong, rather than the nornir wrapper around it."""
    for item in reversed(list(result)):
        if item.exception is not None:
            return item.exception
    return None


def _record_failure(
    records: Dict[str, Dict[str, Any]],
    name: str,
    result,
    log: DebugLog,
    debug: bool,
    prefix: str = "",
) -> None:
    """One readable line for the terminal; the whole story for the log."""
    exc = _exception_of(result)
    summary = prefix + (summarize(exc) if exc is not None else "unknown error")
    records[name]["error"] = summary
    records[name]["status"] = "failed"
    if debug and exc is not None:
        records[name]["traceback"] = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
    log.failure(name, summary, exc)


def _report_text(
    args: argparse.Namespace,
    feature: Feature,
    desired: Desired,
    dry_run: bool,
    records: Dict[str, Dict[str, Any]],
    change: Optional[str] = None,
) -> str:
    """The JSON report, scrubbed. Written by --report and attached to a change."""
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feature": feature.name,
        "mode": args.mode,
        "dry_run": dry_run,
        "change": change,
        "desired": desired.keys,
        "variables": desired.variables,
        "devices": records,
    }
    # Serialize then scrub, covering both the raw secret and its JSON-escaped
    # form -- a password containing a quote or a backslash is written escaped.
    text = json.dumps(document, indent=2, sort_keys=True, default=str)
    escaped = [json.dumps(secret)[1:-1] for secret in desired.secrets]
    return scrub(text, list(desired.secrets) + escaped) + "\n"
