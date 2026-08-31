"""Feature registry.

Each module here exposes a ``FEATURE``; listing it below is all that is needed
to give it a subcommand. Syslog, AAA and friends slot in the same way.
"""

from __future__ import annotations

from typing import Dict

from ..core import Feature
from . import acl, banner, nac, ntp, snmp, snmp_packetsize, syslog, users

FEATURES: Dict[str, Feature] = {
    f.name: f for f in (
        ntp.FEATURE,
        syslog.FEATURE,
        banner.FEATURE,
        acl.FEATURE,
        nac.FEATURE,
        users.FEATURE,
        snmp.FEATURE,
        snmp_packetsize.FEATURE,
    )
}
