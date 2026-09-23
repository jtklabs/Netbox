"""Discovery plugin — onboard a device by typing its IP address.

The job this does: someone racks a switch, types its management address into a
form, and comes back to a fully populated device in NetBox. Nothing else is
asked of them — not the site, not the model, not the serial. All of that is
either already known or is the device's to report.

How the address alone is enough:

    address -> the prefix that contains it -> that prefix's site
            -> the site's (or its region's) poller-<name> tag
            -> the poller that will do the scanning

That chain already existed; it is how `scripts/snmp-inventory` decides what to
scan on its sweeps. This plugin runs it in the other direction — given one
address, whose job is it? — and gives the answer somewhere to live while the
work happens.

Pollers pull, they are never pushed to. They sit at remote sites, often behind
a firewall that allows outbound only, so they check in on their own schedule
and take the work waiting for them. A request therefore has a dwell time: the
UI shows which poller owns it and when that poller was last heard from, so
"nothing has happened yet" is legible rather than mysterious.

Nothing reaches DCIM without a person agreeing to it. The poller reports what
it found and the request waits in `review` until somebody applies it. That is
deliberate — the pipeline this replaced applied everything automatically
because its review queue was a paid feature, and onboarding is exactly where a
wrong site or a duplicate serial is cheapest to catch.

Applying is done by the poller, not by NetBox. All the idempotent create logic
— device types, stacks into virtual chassis, module bays, interfaces, addresses
— lives in the scanner already, and reimplementing it here would be a second
source of truth that could drift from the first.

Discovery rules follow the same split. A rule ("when the name contains fw- and
the model is empty, set the model to FPR-2120") is stored and edited here, and
handed to every poller at the start of its run; the poller applies it to each
scan before reporting, and the request page shows which values a rule
supplied. Nothing here evaluates a rule.

So does the list of stripped domains: which part of a reported hostname is
the domain ("google.com" turns test.google.com into test, and leaves the dots
in sw1.floor2 alone) is kept here and applied by the pollers when they name
what they scan.
"""

from netbox.plugins import PluginConfig


class DiscoveryConfig(PluginConfig):
    name = 'netbox_discovery'
    verbose_name = 'Discovery'
    description = 'Onboard devices by IP address, discovered by remote SNMP pollers'
    version = '0.1.0'
    author = 'Nova Team'
    author_email = 'noreply@example.com'
    base_url = 'discovery'
    min_version = '4.6.0'
    default_settings = {
        # A poller that has not checked in for this long is shown as stale, and
        # requests waiting on it are flagged rather than sitting silently.
        # Comfortably longer than a sensible check-in interval.
        'poller_stale_after_minutes': 30,
        # How long a claimed request may stay claimed before another check-in
        # may take it. Covers a poller that died mid-scan; without it the
        # request would be stuck in `scanning` forever.
        'claim_timeout_minutes': 30,
        # Tag slug prefix that assigns sites and regions to pollers. Must match
        # the scanner's; changing one without the other breaks the mapping.
        'poller_tag_prefix': 'poller-',
        # Region that catches addresses no prefix claims. Its poller-<name> tag
        # names the poller that scans them; the site is chosen at review, since
        # an unplaceable address has none. Empty disables the fallback and
        # unmatched addresses are refused instead.
        'default_region': 'us',
        # 'exceptions' applies a clean scan straight away and holds only the
        # cases in review.py — no model, no site, a serial already on another
        # device, or a failed scan. 'always' sends every device to review.
        # Reviewing everything sounds safer and is not: it teaches people to
        # click Apply without reading.
        'review_policy': 'exceptions',
        # Device role slugs from the top of the network down. A cable between
        # devices of different tiers makes the upper device's upgrade wait for
        # the lower one's. Roles not listed take no part.
        'upgrade_tier_roles': ['core', 'distribution', 'access'],
        # When a device's upgrade fails, hold the other pending jobs at its
        # site in the same batch until a person releases them, in addition to
        # its partners and dependents.
        'hold_site_on_failure': True,
        # Largest show-command output the API accepts, in bytes. Larger files
        # stay on the poller and are listed here with a note.
        'command_output_max_bytes': 16 * 1024 * 1024,
        # How often the worker runs the prestage policies, in minutes. Each
        # policy's own interval decides when a device is checked again; this
        # only bounds how soon a changed standard is picked up. 0 turns the
        # schedule off (Run now still works).
        'prestage_interval_minutes': 60,
    }

    def ready(self):
        super().ready()
        _register_prestage_system_job()


def _register_prestage_system_job():
    """Put the prestage run on the worker's schedule, as NetBox does for housekeeping.

    The rqworker enqueues everything in registry['system_jobs'] at startup and
    JobRunner re-enqueues after each run. Registered here rather than with
    @system_job because the interval comes from settings.
    """
    from netbox.registry import registry

    from netbox_discovery.utils import plugin_setting

    try:
        interval = int(plugin_setting('prestage_interval_minutes') or 0)
    except (TypeError, ValueError):
        interval = 0
    if interval <= 0:
        return

    from netbox_discovery.jobs import PrestageJob

    registry['system_jobs'][PrestageJob] = {'interval': interval}


config = DiscoveryConfig
