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
    }


config = DiscoveryConfig
