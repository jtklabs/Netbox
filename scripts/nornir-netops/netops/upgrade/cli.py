"""Upgrade CLI integration with the standards tool's inventory and credentials."""

import math
import os

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
    parser.set_defaults(workers=3)


def run(args, style):
    from ..cli import _connect, PROJECT_ROOT
    from .profile import Profile
    from .progress import Reporter, settings_from_env
    from .workflow import upgrade_device

    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    profile = Profile.load(args.profile)
    settings = settings_from_env()
    args.standards = load_standards(args.standards, PROJECT_ROOT)
    args.lock_dir = PROJECT_ROOT / ".upgrade-locks"
    targets, credentials, code = _connect(args, style)
    if targets is None:
        return code
    reporter = Reporter(archive.current(), settings)
    print(f"{'APPLY' if args.apply else 'DRY RUN'} {'STAGE ONLY' if args.stage_only else 'UPGRADE'}: {profile.name}; "
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
