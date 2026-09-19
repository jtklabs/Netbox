"""SNMPv3 inventory scanner for NetBox.

Collects hardware and interface inventory over SNMPv3 and writes it into NetBox
through the REST API. See the README for deployment and the module docstrings
for the reasoning behind each layer:

    mibs.py       numeric OIDs and the ifType -> NetBox interface type map
    vendors.py    per-vendor OIDs, software version extraction, Aruba AP table
    snmp.py       net-snmp subprocess wrapper and output parsing
    collect.py    walks a device into structured facts
    model.py      turns facts into NetBox-shaped records (stacks, modules, APs)
    selection.py  works out which addresses this poller owns, from NetBox tags
    netbox.py     REST client with lookup-or-create and dry-run
    sync.py       idempotent writes
    rules.py      fills in what a device does not report, from rules in NetBox
    naming.py     hostname -> device name: which domains come off, from NetBox
    config.py     poller config and SNMPv3 credential sets

The version is reported to the Discovery plugin at every check-in and shown
against the poller in NetBox, so bump it with every change a poller must be
running to benefit from -- it is how "is that poller on the new build yet?" is
answered without logging in to it.

What is reported is build_version(), not the bare number. The number is bumped
by hand, and hand bumps get forgotten: it read 1.0.0 across the first forty
changes, so every poller showed 1.0.0 whatever it was running. The fingerprint
after the "+" moves whenever the code does, bump or no bump.
"""

import hashlib
from pathlib import Path

__version__ = "1.4.0"


def build_version() -> str:
    """__version__ plus a fingerprint of the source that is actually running.

    Hashed from the files rather than read from git because a poller is
    installed by copying this directory — there is no checkout on it to ask.
    `snmp_inventory.py --version` prints the same string from any copy, which
    is what a poller's reading in NetBox is compared against.
    """
    package = Path(__file__).resolve().parent
    sources = sorted(package.glob("*.py"))
    entry_point = package.parent / "snmp_inventory.py"
    if entry_point.is_file():
        sources.append(entry_point)
    digest = hashlib.sha256()
    try:
        for source in sources:
            digest.update(source.name.encode())
            digest.update(source.read_bytes())
    except OSError:
        return __version__
    return f"{__version__}+{digest.hexdigest()[:7]}"
