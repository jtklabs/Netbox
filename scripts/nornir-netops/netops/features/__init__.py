"""Feature registry.

Each module here exposes a ``FEATURE``; listing it below is all that is needed
to give it a subcommand. Syslog, AAA and friends slot in the same way.
"""

from __future__ import annotations

from typing import Dict

from ..core import Feature
from . import ntp, snmp_packetsize, users

FEATURES: Dict[str, Feature] = {
    f.name: f for f in (ntp.FEATURE, users.FEATURE, snmp_packetsize.FEATURE)
}
