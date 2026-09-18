"""Upgrade CLI integration with the standards tool's inventory and credentials."""

import math
import os
import sys

from .. import archive
from ..standards import load as load_standards


def positive(value):
    import argparse
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def add_arguments(parser, scheduled=False):
    parser.add_argument("--profile", required=not scheduled, help="YAML describing a locally validated model/version/image path")
    parser.add_argument("--apply", action="store_true", help="perform the selected operation; default is read-only")
    parser.add_argument("--stage-only", action="store_true", help="only check/copy/verify the image on active flash; never install or reload (copy requires --apply)")
    parser.add_argument("--allow-config-mismatch", action="store_true",
                        help="continue when running and startup configuration still differ after write memory; the difference is recorded in the report")
    parser.add_argument("--standards", default=os.environ.get("NETOPS_STANDARDS"), help="shared standards file for NetBox settings")
    parser.add_argument("--show-timeout", type=positive, default=60, help="seconds per show command")
    parser.add_argument("--config-timeout", type=positive, default=300, help="seconds for show running-config/startup-config, the slowest reads on a large stack")
    parser.add_argument("--install-timeout", type=positive, default=1800, help="seconds for each copy, checksum or install operation")
    parser.add_argument("--reload-timeout", type=positive, default=1800, help="seconds to reconnect on the target software")
    parser.add_argument("--settle-seconds", type=positive, default=120, help="initial post-reload settling time")
    parser.add_argument("--validation-timeout", type=positive, default=600, help="window for two passing post-checks")
    parser.add_argument("--poll-interval", type=positive, default=30, help="seconds between reconnect/convergence checks")
    from ..features.waf import add_connection_arguments
    add_connection_arguments(parser)
    bigip = parser.add_argument_group("BIG-IP upgrades")
    bigip.add_argument("--ucs-dir", default=os.environ.get("NETOPS_UCS_DIR"),
                       help="download each unit's pre-upgrade UCS archive here; otherwise it stays on the unit [$NETOPS_UCS_DIR]")
    parser.add_argument("--image-cache", default=os.environ.get("NETOPS_IMAGE_CACHE"),
                        help="worker directory for images fetched from an image_source URL: BIG-IP uploads and the SCP fallback "
                             "when a switch cannot copy the image itself [$NETOPS_IMAGE_CACHE; default: <project>/.images]")
    parser.add_argument("--ignore-groups", action="store_true",
                        help="proceed even when the targets include more members of a NetBox redundancy group than may upgrade at once")
    parser.set_defaults(workers=3)


def fetch_groups(args, style):
    """Redundancy groups from the discovery plugin, or [] when NetBox does not serve them."""
    from ..credentials import fetch_json_secret
    from ..netbox import Client, NetBoxError, settings_from
    settings = settings_from(args.standards, args)
    if args.netbox_secret:
        document = fetch_json_secret(args.netbox_secret, args.aws_region)
        settings["token"] = settings["token"] or document.get("token")
        settings["url"] = settings["url"] or document.get("url")
    try:
        return Client(settings["url"], settings["token"], settings["verify_tls"]).get("plugins/discovery/upgrade-groups/")
    except NetBoxError as exc:
        print(style.warn(f"redundancy groups not checked: {exc}"), file=sys.stderr)
        return []


def group_conflicts(groups, hosts):
    """Lines describing groups whose selected members exceed their limit."""
    selected = {host.data.get("netbox_id"): host.name for host in hosts if host.data.get("netbox_id") is not None}
    conflicts = []
    for group in groups:
        members = [selected[m["id"]] for m in group.get("members", []) if m.get("id") in selected]
        # A missing limit is a pair; 0 lets every member go at once.
        limit = group.get("max_concurrent")
        limit = 1 if limit is None else int(limit)
        if limit and len(members) > limit:
            conflicts.append(f"redundancy group {group.get('name')!r} allows {limit} member(s) at a time; "
                             f"selected: {', '.join(sorted(members))}")
    return conflicts


def run(args, style):
    from ..cli import _connect, PROJECT_ROOT
    from .profile import Profile
    from .progress import Reporter, settings_from_env
    from .workflow import upgrade_device

    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    from ..features.waf import connection_settings
    profile = Profile.load(args.profile)
    args.f5 = connection_settings(args)
    settings = settings_from_env()
    args.standards = load_standards(args.standards, PROJECT_ROOT)
    args.lock_dir = PROJECT_ROOT / ".upgrade-locks"
    targets, credentials, code = _connect(args, style)
    if targets is None:
        return code
    if args.netbox and not getattr(args, "ignore_groups", False):
        conflicts = group_conflicts(fetch_groups(args, style), targets.inventory.hosts.values())
        if conflicts:
            for line in conflicts:
                print(style.bad(f"error: {line}"), file=sys.stderr)
            print(style.dim("run the members separately, schedule them through NetBox, or pass --ignore-groups"), file=sys.stderr)
            return 3
    reporter = Reporter(archive.current(), settings)
    family = {"f5": "BIG-IP", "eos": "Arista EOS"}.get(profile.family, profile.family)
    print(f"{'APPLY' if args.apply else 'DRY RUN'} {'STAGE ONLY' if args.stage_only else 'UPGRADE'}: {profile.name} ({family}); "
          f"{len(targets.inventory.hosts)} device(s), {args.workers} at a time")
    print(f"credentials: {credentials.describe()}")
    if not settings:
        print("Upgrade webhook is not configured; progress will be saved locally.")
    try:
        # Seed the UI with the full queue before any device begins.
        for host in targets.inventory.hosts.values():
            reporter.emit(host, "queued", "Waiting for an upgrade worker")
        results = targets.run(task=upgrade_device, profile=profile, options=args, reporter=reporter)
        return 1 if results.failed or reporter.delivery_failed else 0
    finally:
        targets.close_connections()
