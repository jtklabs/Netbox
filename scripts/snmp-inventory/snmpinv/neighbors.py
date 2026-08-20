"""Turn raw CDP/LLDP sightings into de-duplicated adjacency candidates.

This module owns the three problems that make neighbor data messy, and none
of them involve NetBox — which keeps all of it testable against fixtures:

  one link, two protocols   A Cisco box answers both CDP and LLDP, so every
                            Cisco-to-Cisco link arrives twice. The sightings
                            are merged into one adjacency that remembers both.

  two spellings, one port   CDP reports interface names long
                            ("GigabitEthernet1/0/1"), LLDP usually short
                            ("Gi1/0/1"), and the scanner stored whatever
                            ifName said. canonical_port() maps the documented
                            Cisco long<->short pairs onto one comparable form.
                            An *unlisted* prefix passes through unchanged, so
                            the failure mode for exotic hardware is a reported
                            non-match, never a wrong match.

  not everything is a switch   Phones, APs and servers announce themselves as
                            neighbors too, and drawing cables to them is a
                            choice, not a given. classify() reads the
                            capability bits and the platform string the
                            neighbor supplied about itself; the sync layer
                            filters on the class, conservatively by default.

What this module deliberately does not do: match anything against NetBox.
Resolution of an adjacency onto Devices and Interfaces needs live NetBox data
and lives in cables.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .collect import Neighbor

log = logging.getLogger(__name__)

# Cisco interface-name prefixes, long form as ifDescr/CDP print it and short
# form as ifName/LLDP print it. Only pairs that are stable across IOS/IOS-XE
# documentation and everyday CLI output are listed; anything absent passes
# through canonical_port() untouched, which turns "we did not know this
# prefix" into a visible unmatched port instead of a silently wrong cable.
# Matching is longest-prefix-first so TwentyFiveGigE wins over
# TwoGigabitEthernet and TenGigabitEthernet over TenGigE.
_LONG_TO_SHORT = tuple(sorted((
    ("twentyfivegige", "twe"),
    ("twogigabitethernet", "tw"),
    ("fivegigabitethernet", "fi"),
    ("tengigabitethernet", "te"),
    ("tengige", "te"),                  # IOS-XR spells 10G this way
    ("fortygigabitethernet", "fo"),
    ("hundredgige", "hu"),
    ("appgigabitethernet", "ap"),
    ("gigabitethernet", "gi"),
    ("fastethernet", "fa"),
    ("ethernet", "et"),
    ("port-channel", "po"),
    ("loopback", "lo"),
    ("tunnel", "tu"),
    ("vlan", "vl"),
    ("serial", "se"),
    ("mgmteth", "mg"),                  # IOS-XR management port
), key=lambda pair: -len(pair[0])))

_MAC_SHAPED = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def canonical_port(name: str) -> str:
    """One comparable spelling for an interface name.

    Lowercases, collapses whitespace, and compresses a known Cisco long
    prefix to its short form — so "GigabitEthernet1/0/1", "Gi1/0/1" and
    "gigabitethernet1/0/1" all become "gi1/0/1", while "ge-0/0/0" (Juniper)
    and "Ethernet1" (Arista, to "et1") map consistently from either side.
    For comparison only: never write a canonical form into NetBox.
    """
    cleaned = " ".join((name or "").split()).lower()
    for long_form, short_form in _LONG_TO_SHORT:
        if cleaned.startswith(long_form):
            rest = cleaned[len(long_form):]
            # Require the prefix to end where the numbering starts, so a
            # hypothetical "EthernetSwitchModule3" is not half-translated.
            if rest[:1].isdigit():
                return short_form + rest
    return cleaned


def short_name(name: str) -> str:
    """A hostname with any DNS suffix removed, lowercased, for matching.

    "sw1", "SW1" and "sw1.corp.example.com" are one device; which spelling a
    protocol reports depends on the neighbor's domain-name configuration, not
    on anything meaningful.
    """
    return (name or "").strip().split(".")[0].lower()


# Neighbor classes, in the order classify() checks them. Phones and APs are
# checked before "network" because a phone reports bridge+telephone and an AP
# bridge+wlanAccessPoint — the bridge bit alone would misfile both.
CLASS_NETWORK = "network"
CLASS_PHONE = "phone"
CLASS_AP = "ap"
CLASS_HOST = "host"
CLASS_UNKNOWN = "unknown"

# Platform substrings that identify a class when capability bits are absent
# or undecodable (CDP's capability word is spec-defined, not MIB-defined, so
# the platform string — the device naming itself in its own words — is the
# primary CDP signal).
_PHONE_PLATFORM_HINTS = ("ip phone", "telecaster")
_AP_PLATFORM_HINTS = ("air-", "aironet", "access point")

_NETWORK_CAPS = {"bridge", "router", "switch", "transparentBridge",
                 "sourceRouteBridge", "repeater"}
_HOST_CAPS = {"host", "stationOnly"}


@dataclass
class Adjacency:
    """One physical link as seen from the scanned device, protocols merged."""

    local_port: str
    local_if_index: int | None
    local_port_source: str
    remote_name: str                # as reported; matching applies suffix tolerance
    remote_port: str                # best name-flavoured remote port string
    remote_port_source: str         # which field remote_port came from
    remote_port_is_mac: bool = False
    chassis_mac: str = ""           # LLDP chassis id when its subtype said MAC
    mgmt_address: str = ""          # CDP management address, when IPv4
    platform: str = ""
    capabilities: frozenset = frozenset()
    capabilities_raw: str = ""
    protocols: tuple = ()
    sightings: list[Neighbor] = field(default_factory=list)

    def describe(self) -> str:
        """One line for logs and reports."""
        via = "+".join(self.protocols)
        remote = self.remote_name or self.chassis_mac or "?"
        return (f"{self.local_port or '?'} -> {remote} port {self.remote_port or '?'}"
                f" ({via})")


def classify(adjacency: Adjacency) -> str:
    """What kind of thing the neighbor says it is.

    Read from what the neighbor reported about itself — capability bits and
    its own platform string — never inferred from its name. Order matters:
    phones and APs both carry the bridge bit, so they are recognised first.
    """
    caps = adjacency.capabilities
    platform = (adjacency.platform or "").lower()
    if "telephone" in caps or any(h in platform for h in _PHONE_PLATFORM_HINTS):
        return CLASS_PHONE
    if "wlanAccessPoint" in caps or any(h in platform for h in _AP_PLATFORM_HINTS):
        return CLASS_AP
    if caps & _NETWORK_CAPS:
        return CLASS_NETWORK
    if caps & _HOST_CAPS:
        return CLASS_HOST
    return CLASS_UNKNOWN


def build_adjacencies(neighbors: list[Neighbor]) -> list[Adjacency]:
    """Merge per-protocol sightings into one adjacency per link.

    Grouping is by local port, then by remote identity within the port —
    a port can genuinely have several neighbors (a phone with a PC behind
    it), so the local port alone is not the key. Two sightings are the same
    link when their remote ports canonicalise to the same name, or when the
    remote system names match short-form and neither protocol reported a
    second device on that port.
    """
    by_port: dict[object, list[Neighbor]] = {}
    for neighbor in neighbors:
        key = neighbor.local_if_index if neighbor.local_if_index is not None \
            else canonical_port(neighbor.local_port)
        by_port.setdefault(key, []).append(neighbor)

    adjacencies: list[Adjacency] = []
    for sightings in by_port.values():
        adjacencies.extend(_merge_port(sightings))
    adjacencies.sort(key=lambda a: (a.local_if_index or 0, a.local_port))
    return adjacencies


def _merge_port(sightings: list[Neighbor]) -> list[Adjacency]:
    groups: list[list[Neighbor]] = []
    for sighting in sightings:
        target = None
        for group in groups:
            if any(_same_remote(sighting, other) for other in group):
                target = group
                break
        if target is None:
            groups.append([sighting])
        else:
            target.append(sighting)
    return [_merge_group(group) for group in groups]


def _same_remote(a: Neighbor, b: Neighbor) -> bool:
    """Do two sightings on one local port describe the same far end?

    Matching remote port names settle it outright. When they disagree — a
    phone's LLDP portDesc says "SW PORT" while its CDP devicePort says
    "Port 1" — a matching system name still identifies one box, and one box
    at the far end of one local port is one link. Only when neither ports nor
    names are comparable are the sightings kept distinct rather than guessed
    together.
    """
    port_a, port_b = _name_flavoured_port(a), _name_flavoured_port(b)
    if port_a and port_b and canonical_port(port_a) == canonical_port(port_b):
        return True
    name_a, name_b = short_name(a.sys_name), short_name(b.sys_name)
    if name_a and name_b:
        return name_a == name_b
    return False


def _name_flavoured_port(sighting: Neighbor) -> str:
    """The remote port as a NAME, or "" when only a MAC (or nothing) came.

    CDP's devicePort is always a name. LLDP's portId is a name only under
    the interfaceName/interfaceAlias/local subtypes; under macAddress the
    name, if anywhere, is in portDesc — Cisco phones do exactly that.
    """
    if sighting.protocol == "cdp":
        return sighting.port_id
    if sighting.port_id and not _MAC_SHAPED.match(sighting.port_id):
        return sighting.port_id
    if sighting.port_desc and not _MAC_SHAPED.match(sighting.port_desc):
        return sighting.port_desc
    return ""


def _merge_group(group: list[Neighbor]) -> Adjacency:
    """Fold one link's sightings together, keeping the richer value per field.

    LLDP is preferred for the chassis identity (its subtype column says what
    the id IS); CDP is preferred for platform and management address (LLDP
    has no platform at all). The remote port prefers whichever protocol
    supplied an actual interface name over a MAC.
    """
    lldp = next((s for s in group if s.protocol == "lldp"), None)
    cdp = next((s for s in group if s.protocol == "cdp"), None)
    primary = lldp or cdp

    # Remote port, best source first: an LLDP portId that IS a name, then
    # CDP's devicePort (always the port's own name), then LLDP's portDesc
    # (descriptive text, e.g. a phone's "SW PORT"), and only then a bare MAC.
    candidates = []
    for sighting in group:
        if sighting.protocol == "lldp":
            if sighting.port_id and not _MAC_SHAPED.match(sighting.port_id):
                candidates.append((0, sighting.port_id, "lldpRemPortId"))
            if sighting.port_desc and not _MAC_SHAPED.match(sighting.port_desc):
                candidates.append((2, sighting.port_desc, "lldpRemPortDesc"))
        elif sighting.port_id:
            candidates.append((1, sighting.port_id, "cdpCacheDevicePort"))
    remote_port, port_source, port_is_mac = "", "", False
    if candidates:
        _, remote_port, port_source = min(candidates)
    elif primary is not None and primary.port_id:
        remote_port = primary.port_id
        port_source = "lldpRemPortId macAddress"
        port_is_mac = bool(_MAC_SHAPED.match(primary.port_id))

    chassis_mac = ""
    if lldp is not None and _MAC_SHAPED.match(lldp.chassis_id or ""):
        chassis_mac = lldp.chassis_id

    capabilities = frozenset().union(*(s.capabilities for s in group))
    local = max(group, key=lambda s: s.local_if_index is not None)
    return Adjacency(
        local_port=local.local_port,
        local_if_index=local.local_if_index,
        local_port_source=local.local_port_source,
        remote_name=next((s.sys_name for s in group if s.sys_name), ""),
        remote_port=remote_port,
        remote_port_source=port_source,
        remote_port_is_mac=port_is_mac,
        chassis_mac=chassis_mac,
        mgmt_address=next((s.mgmt_address for s in group if s.mgmt_address), ""),
        platform=next((s.platform for s in group if s.platform), ""),
        capabilities=capabilities,
        capabilities_raw=next((s.capabilities_raw for s in group if s.capabilities_raw), ""),
        protocols=tuple(sorted({s.protocol for s in group})),
        sightings=list(group),
    )
