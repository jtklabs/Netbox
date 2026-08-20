"""Resolve confirmed adjacencies into NetBox Cable objects — carefully.

Cabling is where discovery can do real damage: a wrongly matched neighbor
draws a cable between two ports that are not connected, and unlike a wrong
MTU nobody spots it until they are standing in front of the rack. So this
module runs on one rule inherited from the rest of the scanner: **report,
never guess**. Every resolution step — which Device the neighbor is, which
Interface its port is — either produces exactly one answer or produces a
report line and no write.

The safety decisions, each of which exists because the friendly alternative
fails silently:

  check first, never create-and-catch   NetBox 4.6 answers "Duplicate
        termination" 400 for a rescan, for the same link created from the
        other side, AND for a genuine conflict — indistinguishably (see
        docs/API-NOTES.md). Reading both interfaces first is what tells
        "already converged" apart from "drift".

  drift is reported, not repaired       An existing cable that disagrees
        with the observed adjacency means either the wiring changed or the
        documentation was hand-drawn on purpose. Both need a person; a
        scanner silently re-wiring NetBox destroys the one and tramples the
        other.

  disappearance is flagged, not deleted A previously discovered cable whose
        adjacency is gone could be a removed link — or a neighbor that was
        simply down during the scan, an LLDP timer expiring, a maintenance
        window. Deleting on absence turns every flapping link into churn.

  scanner cables are tagged             Everything created here carries the
        `discovered` tag, so scanner-drawn documentation stays a queryable,
        bulk-removable class distinct from what humans drew.

  one-sided sightings stay sightings    A neighbor NetBox has never heard of
        is reported as pending; no device is fabricated to terminate its
        cable on.

What gets cabled at all is a policy choice: phones, APs and servers announce
themselves as neighbors too. The class filter defaults to network gear only
(`cable_neighbor_classes = network`), and everything it excludes is counted
and reported rather than dropped.

Known honest limitation, documented rather than papered over: an unmanaged
switch or media converter between two managed devices is invisible to both
protocols, so the adjacency they report — and the cable this draws — is
"direct" even though the path is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .model import ScanResult
from .neighbors import (
    Adjacency,
    build_adjacencies,
    canonical_port,
    classify,
    short_name,
)
from .netbox import NetBox, NetBoxError

log = logging.getLogger(__name__)

# Constant, not a setting, for the same reason as PRIMARY_IP_INTERFACE_NAME:
# rescans and the stale-cable check must find the cables an earlier run made,
# and an operator changing the tag between runs would orphan them.
DISCOVERED_TAG = "discovered"

# Classes cabled when the operator does not choose. Network gear only:
# cabling a phone or an AP is a modelling decision, not a fact recovery.
DEFAULT_CABLE_CLASSES = ("network",)


@dataclass
class CableReport:
    """What one scan's cable pass did and, more importantly, did not do."""

    created: list[str] = field(default_factory=list)
    converged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    unknown_neighbors: list[str] = field(default_factory=list)
    unmatched_ports: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    filtered: list[str] = field(default_factory=list)
    husks_removed: list[str] = field(default_factory=list)

    def merge(self, other: CableReport) -> None:
        for name in vars(self):
            getattr(self, name).extend(getattr(other, name))

    def summary(self) -> str:
        parts = [f"{len(self.created)} created", f"{len(self.converged)} in place"]
        for label, entries in (("conflicts", self.conflicts),
                               ("stale", self.stale),
                               ("unknown neighbors", self.unknown_neighbors),
                               ("unmatched ports", self.unmatched_ports),
                               ("ambiguous", self.ambiguous),
                               ("filtered", self.filtered),
                               ("husks removed", self.husks_removed)):
            if entries:
                parts.append(f"{len(entries)} {label}")
        return ", ".join(parts)


class CableSyncer:
    """Write one scan's adjacencies as cables, under the rules above."""

    def __init__(self, netbox: NetBox, cable_classes: tuple = DEFAULT_CABLE_CLASSES):
        self.netbox = netbox
        self.cable_classes = tuple(cable_classes)
        self.report = CableReport()
        self._tag_ready = False
        self._husks_swept = False
        # One scan's worth of remote interface lists. A distribution switch
        # sees the same core on several ports (uplink members, port-channels),
        # and refetching a chassis's whole interface table per adjacency turns
        # one lookup into dozens. Cleared per scanned host: cable state must
        # be re-read between hosts, since the previous host's sync may have
        # cabled the very interfaces this one is about to check.
        self._interface_cache: dict = {}

    # --- entry point --------------------------------------------------------

    def sync_result(self, result: ScanResult, created: list) -> None:
        """Cable one scanned host's adjacencies. `created` is Syncer's list of
        (DeviceRecord, netbox device) pairs, which is what maps a local port
        on a stack onto the member device that physically owns it."""
        facts = result.facts
        if facts is None or not facts.neighbors:
            return
        self._sweep_dangling_husks()
        adjacencies = build_adjacencies(facts.neighbors)
        if any(device.get("id", 0) < 0 for _, device in created):
            # Dry-run placeholders: the local interfaces do not exist to look
            # up, so resolution cannot run. Say what was seen instead.
            for adjacency in adjacencies:
                log.info("[dry-run] adjacency seen: %s (cable resolution needs "
                         "the devices to exist)", adjacency.describe())
            return

        report = CableReport()
        self._interface_cache = {}
        seen_local_ports: dict[int, set] = {}
        for adjacency in adjacencies:
            local = self._local_interface(adjacency, created, report)
            if local is not None:
                device, interface = local
                seen_local_ports.setdefault(device["id"], set()).add(
                    canonical_port(interface["name"]))
                kind = classify(adjacency)
                if kind not in self.cable_classes:
                    report.filtered.append(f"{adjacency.describe()} [{kind}]")
                    log.info("%s: neighbor filtered (class %s, not in "
                             "cable_neighbor_classes): %s",
                             result.host, kind, adjacency.describe())
                    continue
                self._sync_adjacency(adjacency, interface, report)

        self._flag_stale_cables(created, seen_local_ports,
                                bool(adjacencies), report)
        if any(vars(report).values()):
            log.info("%s: cables — %s", result.host, report.summary())
        self.report.merge(report)

    def summary(self) -> str:
        return self.report.summary()

    # --- the local end ------------------------------------------------------

    def _local_interface(self, adjacency: Adjacency, created: list,
                         report: CableReport):
        """The NetBox interface this adjacency was heard on.

        The scan itself just wrote these interfaces, so a miss here is a real
        finding (usually an unresolvable LLDP local port), not a race.
        """
        if not adjacency.local_port:
            report.unmatched_ports.append(
                f"(local) {adjacency.describe()} — local port unresolved "
                f"({adjacency.local_port_source})")
            log.warning("adjacency skipped, local port unresolved (%s): %s",
                        adjacency.local_port_source, adjacency.describe())
            return None
        wanted = canonical_port(adjacency.local_port)
        for record, device in created:
            for interface_record in record.interfaces:
                if canonical_port(interface_record.name) == wanted:
                    interface = self.netbox.first("/dcim/interfaces/", {
                        "device_id": device["id"], "name": interface_record.name,
                    })
                    if interface is None:
                        report.unmatched_ports.append(
                            f"(local) {adjacency.local_port} not in NetBox")
                        return None
                    return device, interface
        report.unmatched_ports.append(
            f"(local) {adjacency.local_port} matched no scanned interface")
        log.warning("adjacency skipped, local port %r is not among the scanned "
                    "interfaces: %s", adjacency.local_port, adjacency.describe())
        return None

    # --- the remote end -----------------------------------------------------

    def _sync_adjacency(self, adjacency: Adjacency, local_interface: dict,
                        report: CableReport) -> None:
        remote_device = self._resolve_remote_device(adjacency, report)
        if remote_device is None:
            return
        if remote_device is AMBIGUOUS:
            return
        remote_interface = self._resolve_remote_interface(
            adjacency, remote_device, report)
        if remote_interface is None:
            return
        self._ensure_cable(adjacency, local_interface, remote_device,
                           remote_interface, report)

    def _resolve_remote_device(self, adjacency: Adjacency, report: CableReport):
        """Which Device the neighbor is: name, then chassis MAC, then
        management address. Exactly one answer or a report.

        Name matching is suffix-tolerant because whether a neighbor reports
        "sw1" or "sw1.corp.example.com" depends on its domain configuration:
        the reported name is tried verbatim (case-insensitively) first, then
        its short form. Falling back to the chassis MAC or the CDP management
        address covers gear whose sysName never made it into NetBox as the
        device name.
        """
        name = (adjacency.remote_name or "").strip()
        if name:
            candidates = [name]
            if short_name(name) != name.lower():
                # Only when a suffix actually came off — the lookup below is
                # already case-insensitive, so a suffixless name needs one try.
                candidates.append(short_name(name))
            for candidate in candidates:
                matches = self.netbox.all("/dcim/devices/", {"name__ie": candidate})
                if len(matches) == 1:
                    return matches[0]
                if len(matches) > 1:
                    where = ", ".join(
                        f"{m['name']} at {(m.get('site') or {}).get('name')}"
                        for m in matches)
                    report.ambiguous.append(f"{adjacency.describe()} — name "
                                            f"matches {len(matches)} devices: {where}")
                    log.warning("adjacency not cabled, remote name %r is "
                                "ambiguous (%s): %s", candidate, where,
                                adjacency.describe())
                    return AMBIGUOUS

        by_mac = self._device_by_mac(adjacency, report)
        if by_mac is not None:
            return by_mac

        by_address = self._device_by_mgmt_address(adjacency)
        if by_address is not None:
            return by_address

        report.unknown_neighbors.append(adjacency.describe())
        log.info("neighbor not in NetBox (recorded as pending, no device "
                 "fabricated): %s", adjacency.describe())
        return None

    def _device_by_mac(self, adjacency: Adjacency, report: CableReport):
        if not adjacency.chassis_mac:
            return None
        rows = self.netbox.all("/dcim/mac-addresses/",
                               {"mac_address": adjacency.chassis_mac})
        devices: dict[int, dict] = {}
        for row in rows:
            if row.get("assigned_object_type") != "dcim.interface":
                continue
            try:
                interface = self.netbox.get(
                    f"/dcim/interfaces/{row['assigned_object_id']}/")
            except NetBoxError:
                continue
            device = interface.get("device") or {}
            if device.get("id"):
                devices[device["id"]] = device
        if len(devices) == 1:
            return next(iter(devices.values()))
        if len(devices) > 1:
            report.ambiguous.append(
                f"{adjacency.describe()} — chassis MAC {adjacency.chassis_mac} "
                f"is on {len(devices)} devices")
            log.warning("adjacency not cabled, chassis MAC %s appears on %d "
                        "devices: %s", adjacency.chassis_mac, len(devices),
                        adjacency.describe())
            return AMBIGUOUS
        return None

    def _device_by_mgmt_address(self, adjacency: Adjacency):
        if not adjacency.mgmt_address:
            return None
        rows = self.netbox.all("/ipam/ip-addresses/",
                               {"address": adjacency.mgmt_address})
        for row in rows:
            if row.get("assigned_object_type") != "dcim.interface":
                continue
            try:
                interface = self.netbox.get(
                    f"/dcim/interfaces/{row['assigned_object_id']}/")
            except NetBoxError:
                continue
            device = interface.get("device") or {}
            if device.get("id"):
                return device
        return None

    def _resolve_remote_interface(self, adjacency: Adjacency, remote_device: dict,
                                  report: CableReport):
        """The far-end Interface, by canonical name across the whole chassis.

        The search spans the virtual chassis when there is one: the neighbor
        names its stack ("sw1"), the name match lands on the master, but a
        port like Gi2/0/1 lives on the member-2 Device — and the cable must
        terminate there, or the stack's wiring is documented onto the wrong
        box. A MAC-flavoured port id falls back to the MAC table instead.
        """
        # A full device object carries virtual_chassis; one recovered from a
        # nested interface serializer may not, so refetch when it is absent.
        if "virtual_chassis" not in remote_device:
            try:
                remote_device = self.netbox.get(
                    f"/dcim/devices/{remote_device['id']}/")
            except NetBoxError:
                pass
        chassis = remote_device.get("virtual_chassis") or {}
        if chassis.get("id"):
            cache_key = ("vc", chassis["id"])
            query = {"virtual_chassis_id": chassis["id"]}
        else:
            cache_key = ("device", remote_device["id"])
            query = {"device_id": remote_device["id"]}
        interfaces = self._interface_cache.get(cache_key)
        if interfaces is None:
            # A cached row's `cable` can go stale if a second adjacency
            # terminates on the SAME remote port — a physical impossibility
            # that only a misreport produces — and the create then falls into
            # the refused-as-conflict path, which is the right place for it.
            interfaces = self.netbox.all("/dcim/interfaces/", query)
            self._interface_cache[cache_key] = interfaces

        if adjacency.remote_port_is_mac:
            matches = [i for i in interfaces
                       if self._interface_has_mac(i, adjacency.remote_port)]
        else:
            wanted = canonical_port(adjacency.remote_port)
            matches = [i for i in interfaces
                       if canonical_port(i.get("name", "")) == wanted]
        if len(matches) == 1:
            return matches[0]
        target = remote_device.get("name") or f"device {remote_device.get('id')}"
        if not matches:
            report.unmatched_ports.append(
                f"(remote) {adjacency.remote_port!r} not found on {target}: "
                f"{adjacency.describe()}")
            log.warning("adjacency not cabled, port %r not found on %s "
                        "(source %s): %s", adjacency.remote_port, target,
                        adjacency.remote_port_source, adjacency.describe())
        else:
            report.ambiguous.append(
                f"{adjacency.describe()} — {len(matches)} interfaces on "
                f"{target} match {adjacency.remote_port!r}")
            log.warning("adjacency not cabled, %d interfaces on %s match %r",
                        len(matches), target, adjacency.remote_port)
        return None

    def _interface_has_mac(self, interface: dict, mac: str) -> bool:
        primary = interface.get("primary_mac_address") or {}
        return (primary.get("mac_address") or "").upper() == mac.upper()

    # --- the cable itself ---------------------------------------------------

    def _ensure_cable(self, adjacency: Adjacency, local: dict,
                      remote_device: dict, remote: dict,
                      report: CableReport) -> None:
        """Create the cable, or recognise it, or report the conflict.

        Check-first is load-bearing: NetBox rejects ANY second cable touching
        an occupied termination with the same 400, so a rescan, the far side
        of an existing link, and genuine drift all look identical to the API.
        Only reading the interfaces first can tell them apart.
        """
        local_cable = (local.get("cable") or {}).get("id")
        remote_cable = (remote.get("cable") or {}).get("id")

        if local_cable and local_cable == remote_cable:
            # Both ends already share one cable — this scan, an earlier scan,
            # the other side's scan, or a human. Converged; nothing to write.
            report.converged.append(adjacency.describe())
            return

        # An occupied end is not automatically drift. When a cabled neighbor
        # is deleted from NetBox, NetBox strips that side's terminations but
        # KEEPS the cable (docs/API-NOTES.md) — leaving a one-ended husk
        # squatting on the surviving port. If the blocking cable is
        # scanner-tagged and its far side terminates on nothing, it documents
        # nothing: release it and let the observed link take the port. A
        # hand-drawn cable in the same state still goes to a person.
        if local_cable and self._release_if_husk(local, local_cable, report):
            local_cable = None
        if remote_cable and self._release_if_husk(remote, remote_cable, report):
            remote_cable = None

        if local_cable or remote_cable:
            detail = self._describe_conflict(adjacency, local, remote,
                                             local_cable, remote_cable)
            report.conflicts.append(detail)
            log.warning("cable DRIFT (nothing changed in NetBox): %s", detail)
            return

        self._ensure_tag()
        payload = {
            "a_terminations": [{"object_type": "dcim.interface",
                                "object_id": local["id"]}],
            "b_terminations": [{"object_type": "dcim.interface",
                                "object_id": remote["id"]}],
            # The protocols only report links they are exchanging PDUs over,
            # so "connected" is observed fact, not optimism.
            "status": "connected",
            "tags": [{"slug": DISCOVERED_TAG}],
            "description": "Discovered via %s by snmp-inventory"
                           % "+".join(adjacency.protocols),
        }
        label = (f"{(local.get('device') or {}).get('name')}:{local.get('name')}"
                 f" <-> {remote_device.get('name')}:{remote.get('name')}")
        try:
            self.netbox.create("/dcim/cables/", payload, label=f"cable {label}")
        except NetBoxError as exc:
            # Most likely a termination occupied by a cable created between
            # our read and our write. Surfaced as a conflict, not an abort.
            report.conflicts.append(f"{label}: {exc}")
            log.warning("cable create refused for %s: %s", label, exc)
            return
        report.created.append(label)

    def _describe_conflict(self, adjacency: Adjacency, local: dict, remote: dict,
                           local_cable, remote_cable) -> str:
        sides = []
        for interface, cable_id in ((local, local_cable), (remote, remote_cable)):
            if not cable_id:
                continue
            peers = ", ".join(
                f"{(p.get('device') or {}).get('name')}:{p.get('name')}"
                for p in interface.get("link_peers") or []) or "?"
            sides.append(f"{(interface.get('device') or {}).get('name')}:"
                         f"{interface.get('name')} is already cabled to {peers}"
                         f" (cable {cable_id})")
        return f"observed {adjacency.describe()}, but " + " and ".join(sides)

    def _ensure_tag(self) -> None:
        if self._tag_ready:
            return
        self.netbox.ensure_tag(DISCOVERED_TAG, name="Discovered")
        self._tag_ready = True

    # --- husks: cables that outlived their terminations ---------------------

    def _release_if_husk(self, interface: dict, cable_id: int,
                         report: CableReport) -> bool:
        """Delete the cable blocking `interface` IF it is a scanner-owned husk.

        Husk means: tagged `discovered` (ours to manage as a class), exactly
        one termination per side as the scanner creates them, and the side
        away from this interface terminating on nothing — its object was
        deleted out from under it. Anything else returns False and stays for
        the drift report.
        """
        try:
            cable = self.netbox.get(f"/dcim/cables/{cable_id}/")
        except NetBoxError:
            return False
        if DISCOVERED_TAG not in [t.get("slug") for t in cable.get("tags") or []]:
            return False
        near = "a_terminations" if interface.get("cable_end") == "A" else "b_terminations"
        far = "b_terminations" if near == "a_terminations" else "a_terminations"
        if len(cable.get(near) or []) != 1 or cable.get(far):
            return False
        detail = (f"cable {cable_id} on "
                  f"{(interface.get('device') or {}).get('name')}:{interface.get('name')}"
                  f" — its far end was deleted from NetBox, so it documented nothing")
        try:
            self.netbox.delete("/dcim/cables/", cable_id,
                               label=f"husk cable {cable_id}")
        except NetBoxError as exc:
            log.warning("could not release %s: %s", detail, exc)
            return False
        report.husks_removed.append(detail)
        log.info("released %s", detail)
        return True

    def _sweep_dangling_husks(self) -> None:
        """Once per run: remove discovered cables with no terminations at all.

        These arise when BOTH ends' interfaces are deleted — NetBox strips the
        terminations and keeps the Cable. With zero ends the husk is invisible
        to every device-scoped filter, so no per-device pass can ever reach
        it; only a tag-wide sweep finds it. Untagged cables in the same state
        are somebody else's and are left alone.
        """
        if self._husks_swept:
            return
        self._husks_swept = True
        if not self.netbox.tag_exists(DISCOVERED_TAG):
            return
        try:
            cables = self.netbox.all("/dcim/cables/", {"tag": DISCOVERED_TAG})
        except NetBoxError as exc:
            log.debug("dangling-husk sweep skipped: %s", exc)
            return
        for cable in cables:
            if cable.get("a_terminations") or cable.get("b_terminations"):
                continue
            detail = f"cable {cable['id']} had no terminations left at all"
            try:
                self.netbox.delete("/dcim/cables/", cable["id"],
                                   label=f"dangling discovered cable {cable['id']}")
            except NetBoxError as exc:
                log.warning("could not remove %s: %s", detail, exc)
                continue
            self.report.husks_removed.append(detail)
            log.info("removed %s", detail)

    # --- disappearance ------------------------------------------------------

    def _flag_stale_cables(self, created: list,
                           seen_local_ports: dict, had_adjacencies: bool,
                           report: CableReport) -> None:
        """Flag discovered cables whose adjacency was not seen this scan.

        Flag, never delete: the far end may simply have been down, or an LLDP
        hold timer expired mid-scan. And only when this device reported at
        least one neighbor — a device whose neighbor tables answered nothing
        has the protocols disabled or filtered, which says nothing about its
        cables, and flagging all of them would turn one disabled feature into
        a page of false drift.
        """
        if not had_adjacencies:
            return
        if not self.netbox.tag_exists(DISCOVERED_TAG):
            return      # nothing discovered has ever been created
        for _record, device in created:
            try:
                cables = self.netbox.all(
                    "/dcim/cables/",
                    {"device_id": device["id"], "tag": DISCOVERED_TAG})
            except NetBoxError as exc:
                log.debug("stale-cable check skipped for %s: %s",
                          device.get("name"), exc)
                continue
            seen = seen_local_ports.get(device["id"], set())
            for cable in cables:
                ours, peer = self._cable_sides(cable, device["id"])
                if ours is None or len(cable.get("a_terminations") or []) > 1 \
                        or len(cable.get("b_terminations") or []) > 1:
                    continue        # multi-termination cables are hand-drawn
                if canonical_port(ours) in seen:
                    continue
                detail = (f"{device.get('name')}:{ours} -> {peer}: cable "
                          f"{cable['id']} was discovered earlier but no "
                          f"CDP/LLDP neighbor was seen there this scan")
                report.stale.append(detail)
                log.warning("%s (link down during the scan, or unplugged — "
                            "left in place)", detail)

    @staticmethod
    def _cable_sides(cable: dict, device_id: int):
        """(our interface name, peer 'device:interface') for a two-ended cable."""
        ours = peer = None
        for side in ("a_terminations", "b_terminations"):
            for termination in cable.get(side) or []:
                obj = termination.get("object") or {}
                if termination.get("object_type") != "dcim.interface":
                    return None, None
                if (obj.get("device") or {}).get("id") == device_id:
                    ours = obj.get("name")
                else:
                    peer = f"{(obj.get('device') or {}).get('name')}:{obj.get('name')}"
        return ours, peer


class _Ambiguous:
    """Sentinel: resolution found several answers, which is worse than none —
    it means any pick would be a guess, so the caller must not fall through
    to the next resolution strategy either."""


AMBIGUOUS = _Ambiguous()
