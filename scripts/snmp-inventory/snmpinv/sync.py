"""Write a scan result into NetBox, creating whatever is missing.

Everything here is idempotent: each object is looked up by its natural key
before being created, and existing objects are patched only where a field
actually differs. Running a scan twice produces no second copy of anything and
no changelog noise.

Two behaviours are deliberate and worth stating plainly, because they are the
reasons this tool exists:

  * The device's reported model wins. If NetBox holds a different device type,
    the device is moved to the one matching what the hardware reported. That is
    the opposite of the previous pipeline, which recreated a wrong model on
    every pass and made hand corrections pointless.

  * A fact we did not collect never blanks a fact somebody entered. Absent is
    not the same as empty — a device that failed to report a serial leaves the
    existing serial alone.

Ordering is forced by NetBox's own referential rules: a VirtualChassis has to
exist before a device can join it, but its master has to be set afterwards
because the master is one of those devices.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .cables import DEFAULT_CABLE_CLASSES, CableSyncer
from .model import DeviceRecord, InterfaceRecord, ModuleRecord, ScanResult
from .netbox import NetBox, NetBoxError
from .vendors import clean_serial

log = logging.getLogger(__name__)

# Name of the virtual interface created to hold a polled address that the
# device itself never reported on any interface. A constant rather than a
# setting: it has to be found again on the next scan, and an operator changing
# it between runs would leave orphans behind.
PRIMARY_IP_INTERFACE_NAME = "mgmt-discovered"

# NetBox's limits, which entPhysicalName cheerfully exceeds. A Nexus line card
# or an F5 blade can describe its own slot in a sentence, and the POST then
# fails on the whole module rather than on the name.
MODULE_BAY_NAME_LENGTH = 64
MODULE_BAY_DESCRIPTION_LENGTH = 200


def _fit(name: str, limit: int) -> str:
    """Shorten a device-supplied name to something NetBox will accept.

    The tail is kept rather than the head. These names are overwhelmingly a
    long common prefix — "Chassis 1 Slot 3 Transceiver ..." — with the part
    that identifies the slot at the end, so truncating from the right produces
    a set of bays that are all the same string and collapse onto one another.
    An ellipsis marks it as shortened; the full text goes in the description.
    """
    if len(name) <= limit:
        return name
    return "…" + name[-(limit - 1):]

SOFTWARE_VERSION_FIELD = "software_version"

# NetBox has no native per-device software version field, so the version needs
# a home. There are two, and which one is used depends on the instance:
#
#   Lifecycle plugin present  -> its DeviceSoftware ingest endpoint. That model
#                                exists for exactly this: it keeps the raw
#                                string, what the reading came from, when it was
#                                taken, and drives compliance against approved
#                                versions. Its /report/ endpoint also bumps an
#                                unchanged version with a queryset update, so a
#                                nightly sweep does not write one changelog
#                                entry per device per night.
#   plugin absent             -> a text custom field on dcim.device, created by
#                                the scanner so a bare NetBox needs no setup.
#
# Deliberately not both. The same fact in two places drifts, and the plugin's
# copy is the one the compliance reporting reads.
LIFECYCLE_PLUGIN = "netbox_refresh"
LIFECYCLE_ENDPOINT = "/plugins/refresh/device-software/"
LIFECYCLE_REPORT_ENDPOINT = f"{LIFECYCLE_ENDPOINT}report/"
LIFECYCLE_SOURCE_SNMP = "snmp"

SOFTWARE_VERSION_CUSTOM_FIELD = {
    "name": SOFTWARE_VERSION_FIELD,
    "label": "Software version",
    "type": "text",
    "object_types": ["dcim.device"],
    "description": "Running OS version, collected over SNMP by scripts/snmp-inventory",
}


# Where a replaced unit's record goes. `inventory` says "we still have this
# metal, it is just not in service", which is what an RMA'd or shelved unit
# actually is; `decommissioning` and `offline` are the other sensible answers
# depending on how the estate is run.
RETIRED_DEVICE_STATUS = "inventory"
RETIRED_TAG = "replaced"
# Put on a device by the Discovery plugin when its request was entered by
# hand. Somebody typed that model and serial because the box could not be
# scanned, and a later scan that resolves to the record must not overwrite
# what they typed: it may add interfaces and addresses, never change identity.
MANUAL_TAG = "discovery-manual"

DEVICES_ENDPOINT = "/dcim/devices/"
# A Nexus VDC is written as a NetBox virtual device context on its chassis.
VDC_ENDPOINT = "/dcim/virtual-device-contexts/"

# A vCMP guest is a device of its own, linked to the chassis it runs on
# through an object custom field the scanner creates on first use, the way
# it creates the software version field. An object field rather than free
# text so the link is clickable and survives the host being renamed.
VCMP_HOST_FIELD = "vcmp_host"
VCMP_HOST_CUSTOM_FIELD = {
    "name": VCMP_HOST_FIELD,
    "label": "vCMP host",
    "type": "object",
    "object_types": ["dcim.device"],
    "related_object_type": "dcim.device",
    "description": "The BIG-IP chassis this vCMP guest runs on, set by the SNMP scanner",
}

REPLACEMENT_ENDPOINT = "/plugins/discovery/hardware-replacements/"


@dataclass
class SyncOptions:
    device_role: str = "network"
    access_point_role: str = "wireless-ap"
    device_status: str = "active"
    sync_interfaces: bool = True
    sync_ips: bool = True
    sync_modules: bool = True
    sync_access_points: bool = True
    set_primary_ip: bool = True
    manage_software_version: bool = True
    # Move a device to the site the scan says it is at, when NetBox disagrees.
    # On by default: the scanned site is itself NetBox-derived — it comes from
    # the site the containing prefix is scoped to — so a mismatch usually means
    # the device was physically relocated and IPAM was updated but the device
    # record was not. Leaving it also allows a virtual chassis to end up split
    # across two sites, which is never right.
    move_devices_between_sites: bool = True
    # A serial that changed under a name we already knew means the metal was
    # swapped. Retiring the old record rather than overwriting its serial keeps
    # the thread on a unit that may still be under support — serials are what
    # contracts and quotes are matched on.
    retain_replaced_hardware: bool = True
    retired_device_status: str = RETIRED_DEVICE_STATUS
    # Write CDP/LLDP adjacencies as Cable objects. The classes tuple is the
    # safety valve: phones, APs and servers announce themselves as neighbors
    # too, and cabling them is a modelling choice — default is network gear
    # only, and everything filtered is reported rather than dropped.
    sync_cables: bool = True
    cable_neighbor_classes: tuple = DEFAULT_CABLE_CLASSES
    # Write HSRP/VRRP groups as NetBox FHRP groups with interface assignments,
    # and ask the discovery plugin to refresh its redundancy groups afterwards.
    sync_fhrp_groups: bool = True


def _primary_address(device: dict) -> str:
    primary = device.get("primary_ip4") or device.get("primary_ip") or {}
    return (primary.get("address") or "").split("/")[0] if isinstance(primary, dict) else ""


class Syncer:
    def __init__(self, netbox: NetBox, options: SyncOptions | None = None,
                 strip_domains: tuple = ()):
        self.netbox = netbox
        self.options = options or SyncOptions()
        # The same list the scan results were named under (naming.py), so a
        # neighbor's reported hostname is looked up under the name its own
        # scan would have given it.
        self.cables = CableSyncer(netbox, self.options.cable_neighbor_classes,
                                  strip_domains=strip_domains)
        self._custom_field_ready = False
        self._vcmp_field_ready = False
        # The routing table of the host being synced; see sync().
        self._vrf_id: int | None = None
        self._use_lifecycle: bool | None = None
        self._replacements_ok: bool | None = None
        # Chassis swaps are detected before the replacement device exists, so
        # the audit row waits here until it has something to point at.
        self._pending_replacements: list[dict] = []
        # Version readings are batched and sent once at the end of a run. The
        # ingest endpoint takes a list, and one call for a fleet beats one call
        # per device across a WAN.
        self._software_reports: list[dict] = []

    # --- entry point --------------------------------------------------------

    def sync(self, result: ScanResult, site_id: int | None, scanned_address: str = "",
             tenant_id: int | None = None, vrf_id: int | None = None) -> None:
        """Write one scanned host — a single device or a whole stack.

        `tenant_id` files the result against the company that owns it. It
        matters most where address space overlaps between us and companies we
        have bought: the tenant is what made the address resolvable in the
        first place, and dropping it here would leave the device unattributed.

        `vrf_id` is the routing table the host was found in — the VRF of the
        prefix that placed it, or None for the global table. Every address
        this host reports is looked up and created in that table and no
        other. In overlapping space the same address exists twice, and a
        lookup across all tables finds the other network's copy: the sync
        would decline to steal it and the device would land with no address,
        or worse, be matched to the other network's device by it.
        """
        # Held for the length of this one sync rather than passed down a dozen
        # signatures. Safe because a Syncer writes one host at a time -- every
        # caller serialises syncs under a lock -- and cleared afterwards so a
        # later host can never inherit it.
        self._vrf_id = vrf_id
        try:
            self._sync(result, site_id, scanned_address, tenant_id)
        finally:
            self._vrf_id = None

    def _in_vrf(self) -> dict:
        """The filter that scopes an address lookup to this sync's routing
        table. NetBox takes `vrf_id=null` for the global table."""
        return {"vrf_id": self._vrf_id if self._vrf_id else "null"}

    def _sync(self, result: ScanResult, site_id: int | None, scanned_address: str,
              tenant_id: int | None) -> None:
        if not result.devices:
            log.warning("%s: nothing to sync (device reported no chassis)", result.host)
            return
        if site_id is None:
            log.warning("%s: no site could be determined, skipping", result.host)
            return

        if self.options.manage_software_version and not self._lifecycle_available():
            self._ensure_software_version_field()

        # A Nexus VDC is not a device: it is a slice of a chassis that must
        # already be in NetBox, and is written onto that record. A vCMP guest
        # is a device and goes through the ordinary path below, linked to its
        # host afterwards.
        primary = result.primary
        if (primary is not None and primary.context is not None and primary.context.is_vdc
                and self._sync_vdc(result, primary, scanned_address, tenant_id)):
            return

        virtual_chassis = None
        if result.is_stack:
            virtual_chassis = self._ensure_virtual_chassis(result, scanned_address)

        created: list[tuple[DeviceRecord, dict]] = []
        for record in result.devices:
            # The polled address belongs to the device that answered -- the
            # master, or the only box. Offered to another stack member it
            # would "find" the master's record for that member, and the
            # serial mismatch would then read as the master being swapped.
            address = scanned_address if record is primary else ""
            device = self._ensure_device(record, site_id, virtual_chassis, tenant_id,
                                         scanned_address=address)
            if device is not None:
                created.append((record, device))
                if record.context is not None and record.context.is_vcmp_guest:
                    self._link_vcmp_host(device, record)

        # The master is one of the devices we just created, so it can only be
        # set once they exist.
        if virtual_chassis is not None:
            self._set_master(virtual_chassis, created)

        for record, device in created:
            if self.options.sync_modules:
                self._sync_modules(device, record)
            if self.options.sync_interfaces:
                self._sync_interfaces(device, record, scanned_address, tenant_id)
            if self.options.manage_software_version:
                self._queue_software_report(device, record, result)

        # After the interfaces, which cables terminate on; gated on both
        # switches because with sync_interfaces off the local ends may not
        # exist and every adjacency would report as unmatched.
        if self.options.sync_cables and self.options.sync_interfaces and created:
            self.cables.sync_result(result, created)

        if self.options.sync_fhrp_groups and self.options.sync_interfaces and result.fhrp_groups:
            self._sync_fhrp_groups(result, created)

        if self.options.sync_access_points and result.access_points:
            self._sync_access_points(result, site_id, tenant_id)

    # --- first-hop redundancy -----------------------------------------------

    FHRP_GROUPS = "/ipam/fhrp-groups/"
    FHRP_ASSIGNMENTS = "/ipam/fhrp-group-assignments/"
    UPGRADE_GROUP_REFRESH = "/plugins/discovery/upgrade-groups/refresh/"

    def _sync_fhrp_groups(self, result: ScanResult, created: list) -> None:
        """One NetBox FHRP group per protocol, group id and virtual address.

        The group id alone is not an identity: HSRP group 1 exists on every
        VLAN of a pair. The virtual address is what makes two devices' rows
        the same group, so it is part of the name and the lookup.
        """
        if not self.netbox.endpoint_available(self.FHRP_GROUPS):
            return
        for group in result.fhrp_groups:
            interface = self._interface_named(created, group.interface)
            if interface is None:
                log.warning("%s: %s group %s on %s: interface not in NetBox, group skipped",
                            result.host, group.protocol.upper(), group.group_id, group.interface)
                continue
            protocol = "hsrp" if group.protocol == "hsrp" else "vrrp2"
            name = f"{protocol.upper()} {group.group_id} {group.virtual_ip}".strip()
            fhrp = self.netbox.ensure(
                self.FHRP_GROUPS, {"protocol": protocol, "group_id": group.group_id, "name": name},
                {"protocol": protocol, "group_id": group.group_id, "name": name,
                 "description": f"discovered by snmp-inventory on {group.interface}"},
                label=f"FHRP group {name}",
            )
            if fhrp is None:
                continue
            existing = self.netbox.first(self.FHRP_ASSIGNMENTS, {
                "group_id": fhrp["id"], "interface_type": "dcim.interface", "interface_id": interface["id"],
            })
            priority = group.priority if group.priority is not None else 100
            if existing is None:
                self.netbox.create(self.FHRP_ASSIGNMENTS, {
                    "group": fhrp["id"], "interface_type": "dcim.interface",
                    "interface_id": interface["id"], "priority": priority,
                }, label=f"FHRP assignment {name} on {group.interface}")
            else:
                self.netbox.ensure_fields(self.FHRP_ASSIGNMENTS, existing, {"priority": priority},
                                          label=f"FHRP assignment {name} on {group.interface}")

    def _interface_named(self, created: list, name: str) -> dict | None:
        for record, device in created:
            if any(interface.name == name for interface in record.interfaces):
                return self.netbox.first("/dcim/interfaces/", {"device_id": device["id"], "name": name})
        return None

    def refresh_upgrade_groups(self) -> None:
        """Ask the discovery plugin to rebuild redundancy groups from FHRP data and cables."""
        if not self.netbox.endpoint_available("/plugins/discovery/upgrade-groups/"):
            log.debug("discovery plugin upgrade groups not available; skipping refresh")
            return
        try:
            counts = self.netbox.post_raw(self.UPGRADE_GROUP_REFRESH, {}, label="upgrade group refresh")
        except NetBoxError as exc:
            log.warning("upgrade group refresh failed: %s", exc)
            return
        if counts:
            log.info("upgrade groups: %s FHRP group(s), %s cabled dependency(ies), %s stale",
                     counts.get("groups"), counts.get("dependencies"),
                     (counts.get("stale_groups") or 0) + (counts.get("stale_dependencies") or 0))

    # --- devices ------------------------------------------------------------

    def _ensure_device(self, record: DeviceRecord, site_id: int,
                       virtual_chassis: dict | None,
                       tenant_id: int | None = None,
                       scanned_address: str = "") -> dict | None:
        manufacturer = self._ensure_manufacturer(record.manufacturer)
        device_type = self._ensure_device_type(manufacturer, record.model, record.part_number)
        role = self._ensure_role(
            self.options.access_point_role if record.is_access_point else self.options.device_role
        )
        platform = self._ensure_platform(record.platform, manufacturer)

        existing = self._find_device(record, site_id, scanned_address, virtual_chassis)
        manual = existing is not None and self._is_manual(existing)

        desired: dict = {}
        if record.serial:
            desired["serial"] = record.serial
        if tenant_id:
            desired["tenant"] = tenant_id
        if device_type:
            desired["device_type"] = device_type["id"]
        if platform:
            desired["platform"] = platform["id"]
        if record.vc_position is not None and virtual_chassis:
            desired["virtual_chassis"] = virtual_chassis["id"]
            desired["vc_position"] = record.vc_position
            if record.vc_is_master:
                # Highest priority so a NetBox-side election agrees with what
                # the stack itself reported.
                desired["vc_priority"] = 255
        if (self.options.manage_software_version and record.software_version
                and not self._lifecycle_available()):
            desired["custom_fields"] = {SOFTWARE_VERSION_FIELD: record.software_version}

        if existing is not None and manual:
            # Entered by hand: the typed identity stands. Only what the record
            # lacks is filled in, and nothing is retired or moved.
            for key in ("serial", "device_type", "site", "tenant"):
                if key in desired and existing.get(key) not in (None, "", {}):
                    desired.pop(key)
            log.debug("%s: entered by hand; identity left as typed", record.name)
            return self._patch_device(existing, desired, record)
        if existing is not None:
            replaced = self._handle_serial_change(existing, record, site_id)
            if replaced is not None:
                # The old record has been retired and given up the name; fall
                # through and create a fresh device for the new hardware.
                existing = None
            else:
                self._log_model_correction(existing, device_type, record)
                self._apply_site_move(existing, site_id, desired, record)
                self._follow_naming_rule(existing, record, site_id, desired)
                return self._patch_device(existing, desired, record)

        if device_type is None:
            # Without a model we cannot pick a device type, and NetBox requires
            # one. Say so loudly rather than inventing a placeholder type that
            # somebody would have to clean up later.
            log.warning(
                "%s: no model reported (ENTITY-MIB empty and no vendor model OID) — "
                "device not created", record.name
            )
            return None
        if role is None:
            log.warning("%s: device role unavailable, skipping", record.name)
            return None

        payload = {
            "name": record.name,
            "device_type": device_type["id"],
            "role": role["id"],
            "site": site_id,
            "status": self.options.device_status,
        }
        payload.update({k: v for k, v in desired.items() if k != "device_type"})
        created = self.netbox.create("/dcim/devices/", payload,
                                     label=f"device {record.name}")
        self._flush_pending_replacements(record, created)
        return created

    @staticmethod
    def _is_this_device(device: dict, record: DeviceRecord, scanned_address: str,
                        virtual_chassis: dict | None = None) -> bool:
        """Does a record that shares this scan's serial agree with it on
        anything else? A rename keeps the address; a re-address keeps the name.

        The name may be one an earlier naming rule gave the device (the first
        label, before its domain was listed to be stripped). And for a stack
        member, sitting in the stack's own virtual chassis under the same
        serial settles it whatever the member has been called.
        """
        stored_name = (device.get("name") or "").strip().lower()
        names = {record.name.strip().lower()} | {n.strip().lower() for n in record.former_names}
        if stored_name and stored_name in names - {""}:
            return True
        if (virtual_chassis is not None and record.vc_position is not None
                and virtual_chassis.get("id") is not None
                and (device.get("virtual_chassis") or {}).get("id") == virtual_chassis["id"]):
            return True
        primary_address = _primary_address(device)
        return bool(scanned_address and primary_address and scanned_address == primary_address)

    def _ensure_virtual_chassis(self, result: ScanResult, scanned_address: str) -> dict | None:
        """The stack's virtual chassis: by name, else the one its master is in.

        A stack whose name changed -- the naming rule changed, or somebody
        renamed the switch -- would otherwise get a second virtual chassis,
        and its master would be dragged out of the one it leads. The master's
        own record says which chassis this is, and that chassis is used
        whatever it is called.
        """
        path = "/dcim/virtual-chassis/"
        name = result.virtual_chassis_name
        found = self.netbox.first(path, {"name": name})
        if found is not None:
            return found
        master = result.primary
        if master is not None and master.serial:
            for device in self._devices_with_serial(master.serial):
                current = device.get("virtual_chassis") or {}
                if not current.get("id") or not self._is_this_device(device, master, scanned_address):
                    continue
                try:
                    chassis = self.netbox.get(f"{path}{current['id']}/")
                except NetBoxError:
                    break
                formers = {n.lower() for n in result.former_chassis_names}
                current_name = chassis.get("name") or ""
                # Only to take a listed domain off, never to lengthen a name;
                # see _follow_naming_rule.
                if current_name.lower() in formers and len(name) < len(current_name):
                    log.info("virtual chassis %r renamed %r to follow the hostname rule",
                             chassis.get("name"), name)
                    return self.netbox.ensure_fields(path, chassis, {"name": name},
                                                     label=f"virtual chassis {name}")
                return chassis
        return self.netbox.create(path, {"name": name}, label=f"virtual chassis {name}")

    def _follow_naming_rule(self, existing: dict, record: DeviceRecord, site_id: int,
                            desired: dict) -> None:
        """Take a newly listed domain off a record the scanner named with it on.

        The case: a domain was missing from the stripped-domain list, a device
        was created as "sw9.other.net", and the domain has been listed since.
        Only a name the scanner itself derived from the hostname
        (record.former_names) is touched; one somebody typed is none of those
        and stays. And only ever to take something OFF: a record is never
        given a longer name than it has, so listing a first domain cannot
        turn every existing "sw1" under an unlisted domain into an FQDN.
        Skipped when the new name is already taken at the site, since NetBox
        would refuse the whole update over it.
        """
        current = (existing.get("name") or "").strip()
        if not current or current.lower() == record.name.strip().lower():
            return
        if current.lower() not in {n.lower() for n in record.former_names}:
            return
        if len(record.name.strip()) >= len(current):
            return
        taken = [d for d in self.netbox.all(DEVICES_ENDPOINT,
                                            {"name__ie": record.name, "site_id": site_id})
                 if d.get("id") != existing.get("id")]
        if taken:
            log.warning("%s: would be renamed %r under the hostname rule, but that name is "
                        "already taken at this site; left as it is", current, record.name)
            return
        log.info("%s: renamed %r to follow the hostname rule", current, record.name)
        desired["name"] = record.name

    def _handle_serial_change(self, existing: dict, record: DeviceRecord,
                              site_id: int) -> dict | None:
        """Retire a device whose serial no longer matches what is at the address.

        A different serial under the same name is a chassis swap — an RMA, or a
        spare pulled off the shelf. Overwriting the serial in place would make
        the old unit vanish from NetBox entirely, and with it any support
        contract or quote matched on that serial. So the old record is kept,
        retired and renamed to free the name, and the caller creates a new
        device for the metal that is actually there now.

        Returns the retired record when a swap happened, else None.
        """
        old_serial = clean_serial(existing.get("serial") or "")
        new_serial = clean_serial(record.serial)
        if not self.options.retain_replaced_hardware:
            return None
        # Only a change between two known serials counts. Filling in a blank is
        # the first successful read, not a replacement — and case-insensitively,
        # like the duplicate-serial check: a collection source that changes the
        # case it reports in is not a chassis swap.
        if not old_serial or not new_serial or old_serial.lower() == new_serial.lower():
            return None

        log.warning(
            "%s: serial changed %s -> %s — the old unit is being retained as a "
            "separate device rather than overwritten",
            record.name, old_serial, new_serial,
        )

        retired_name = self._retired_name(existing.get("name") or record.name, old_serial)
        changes = {
            "name": retired_name,
            "status": self.options.retired_device_status,
            # Free the address so the retired record is not rescanned and does
            # not hold the IP the replacement needs.
            "primary_ip4": None,
        }
        self.netbox.update("/dcim/devices/", existing["id"], changes,
                           label=f"retire replaced device {existing.get('name')}")
        self._tag_retired(existing)
        self._record_replacement(
            kind="chassis", device_id=None, replaced_device_id=existing["id"],
            old_serial=old_serial, new_serial=new_serial, model=record.model,
            pending_for=record,
        )
        return existing

    @staticmethod
    def _retired_name(name: str, old_serial: str) -> str:
        """Free the live name while keeping the retired record recognisable.

        Device names are unique per (name, site, tenant), so the old record has
        to give the name up before the replacement can take it. The serial in
        the suffix is the RETIRED unit's own — it uniquifies a name that gets
        retired more than once over the years.

        Wording matters here and was got wrong once: "[replaced ABC123]" was
        read in the fleet as this device having replaced serial ABC123 — its
        own serial, since that is whose serial it is — when the intent was the
        passive "this record WAS replaced; it was unit ABC123". "retired" is
        unambiguous about which side of the swap this record is on.
        """
        suffix = f" [retired {old_serial}]"
        return (name[: 64 - len(suffix)] + suffix) if len(name) + len(suffix) > 64 else name + suffix

    def _tag_retired(self, device: dict) -> None:
        tags = [t.get("slug") for t in device.get("tags", []) if t.get("slug")]
        if RETIRED_TAG in tags:
            return
        self.netbox.ensure_tag(RETIRED_TAG, name="Replaced")
        self.netbox.update(
            "/dcim/devices/", device["id"],
            {"tags": [{"slug": slug} for slug in tags + [RETIRED_TAG]]},
            label=f"tag {device.get('name')} replaced",
        )

    def _record_replacement(self, kind, device_id, replaced_device_id,
                            old_serial, new_serial, model, pending_for=None,
                            module_bay="") -> None:
        """Log the swap where it can be reported on.

        NetBox's changelog holds the old value too, but only as a diff on one
        object at one moment. This is the queryable form, and for a module it
        is the only surviving trace — Module.module_bay is not nullable, so the
        old row cannot stay once the bay is refilled.
        """
        if not self._replacements_available():
            return
        if device_id is None:
            # The replacement device does not exist yet; hold it until it does.
            self._pending_replacements.append({
                "kind": kind, "replaced_device": replaced_device_id,
                "old_serial": old_serial, "new_serial": new_serial,
                "model_name": model, "module_bay": module_bay,
                "_for_serial": new_serial,
            })
            return
        self._post_replacement({
            "kind": kind, "device": device_id, "replaced_device": replaced_device_id,
            "old_serial": old_serial, "new_serial": new_serial,
            "model_name": model, "module_bay": module_bay,
        })

    def _post_replacement(self, payload: dict) -> None:
        payload.setdefault("detected_at", datetime.now(timezone.utc).isoformat())
        try:
            self.netbox.create(REPLACEMENT_ENDPOINT, payload,
                               label="hardware replacement %s -> %s"
                                     % (payload["old_serial"], payload["new_serial"]))
        except NetBoxError as exc:
            # The inventory is already correct; losing the audit row is not
            # worth failing the scan over, but it must be said out loud.
            log.error("could not record hardware replacement: %s", exc)

    def _replacements_available(self) -> bool:
        if self._replacements_ok is None:
            self._replacements_ok = self.netbox.endpoint_available(REPLACEMENT_ENDPOINT)
            if not self._replacements_ok:
                log.warning(
                    "the Discovery plugin is not installed, so serial changes cannot "
                    "be recorded; replaced devices are still retained"
                )
        return self._replacements_ok

    def _flush_pending_replacements(self, record: DeviceRecord, device: dict) -> None:
        """Attach held replacement rows once the new device exists."""
        if not self._pending_replacements or device is None or device.get("id", 0) < 0:
            return
        remaining = []
        for pending in self._pending_replacements:
            if pending.get("_for_serial") == record.serial.strip():
                payload = {k: v for k, v in pending.items() if not k.startswith("_")}
                payload["device"] = device["id"]
                self._post_replacement(payload)
            else:
                remaining.append(pending)
        self._pending_replacements = remaining

    def _find_device(self, record: DeviceRecord, site_id: int,
                     scanned_address: str = "",
                     virtual_chassis: dict | None = None) -> dict | None:
        """The record this scan belongs to, or None to create one.

        The rule that matters more than any match: a scan never lands on a
        record that might be a different box. The old rules did exactly that
        twice over -- a serial alone claimed the first record carrying it,
        and a name alone claimed a record at any site -- so a serial typed on
        two devices, or two branch switches both called core-sw-01 with no
        serial, quietly overwrote each other. Duplicate serials are allowed
        now (a VDC, a guest, a vendor reusing one) and are never refused or
        reported; what is refused is the guess.

        With a serial: the records carrying it, and among them the one that
        agrees on name or address. A rename keeps the address, a re-address
        keeps the name; a record agreeing on neither is a different box.

        Then, with or without a serial: the device with the polled address on
        one of its interfaces -- an address belongs to one box -- else the
        name at the site being scanned. This is how a record made without a
        serial (entered by hand, imported from a sheet) gets its serial when
        the box first reports one, and how a swapped chassis is noticed: the
        name matches and the serial does not, which _handle_serial_change
        reads as a replacement. Never the name elsewhere: names repeat across
        sites, and matching one moved devices between sites.

        None means a new record, beside whatever carries the serial.
        """
        if record.serial:
            candidates = self._devices_with_serial(record.serial)
            for device in candidates:
                if self._is_this_device(device, record, scanned_address, virtual_chassis):
                    return device
            if candidates:
                log.warning(
                    "%s: serial %s is already on %s, which agrees on neither name nor "
                    "address; %r is treated as a separate device with the same serial",
                    scanned_address or record.name, record.serial,
                    ", ".join(d.get("name") or "device %s" % d.get("id") for d in candidates),
                    record.name,
                )
        by_address = self._device_at_address(scanned_address)
        if by_address is not None:
            return by_address
        if record.name:
            return self.netbox.first(DEVICES_ENDPOINT, {"name": record.name, "site_id": site_id})
        return None

    def _device_at_address(self, address: str) -> dict | None:
        """The device carrying `address` on one of its interfaces, if any."""
        if not address:
            return None
        # In this host's routing table only: the same address in another VRF
        # is another network's device.
        for ip in self.netbox.all("/ipam/ip-addresses/", {"address": address, **self._in_vrf()}):
            if ip.get("assigned_object_type") != "dcim.interface" or not ip.get("assigned_object_id"):
                continue
            assigned = ip.get("assigned_object") or {}
            device_id = (assigned.get("device") or {}).get("id") if isinstance(assigned, dict) else None
            if device_id is None:
                interface = self.netbox.first("/dcim/interfaces/", {"id": ip["assigned_object_id"]})
                device = (interface or {}).get("device") or {}
                device_id = device.get("id") if isinstance(device, dict) else device
            if device_id:
                try:
                    return self.netbox.get(f"{DEVICES_ENDPOINT}{device_id}/")
                except NetBoxError:
                    return None
        return None

    @staticmethod
    def _is_manual(device: dict) -> bool:
        return MANUAL_TAG in {t.get("slug") for t in device.get("tags") or [] if isinstance(t, dict)}

    def _devices_with_serial(self, serial: str) -> list[dict]:
        return self.netbox.all(DEVICES_ENDPOINT, {"serial": serial})

    # --- partitions of a chassis: Nexus VDCs and vCMP guests ----------------

    def _sync_vdc(self, result: ScanResult, record: DeviceRecord,
                  scanned_address: str, tenant_id: int | None) -> bool:
        """Write a Nexus VDC as a virtual device context on its chassis.

        The chassis is found by the serial the VDC reports, which is the
        chassis's own. Its interfaces are the chassis's ports, so they are
        written to the chassis and allocated to the context; the VDC's
        management address becomes the context's primary IP, never the
        chassis's. Modules and the software version are the chassis's and are
        written from its own scan.

        Returns False to have the caller carry on as for an ordinary device.
        That happens when the record this serial resolves to *is* this VDC --
        a chassis first recorded from a non-default VDC, before VDCs were
        understood -- since demoting it to a context of itself would be wrong.
        """
        context = record.context
        candidates = self._devices_with_serial(record.serial) if record.serial else []
        if any(self._is_this_device(d, record, scanned_address) for d in candidates):
            log.warning(
                "%s: NetBox device %r carries this chassis serial and answers as VDC %r, "
                "so it was recorded from a VDC rather than from the default VDC. Treating "
                "it as the chassis. Point its primary IP at the default VDC's address to "
                "have this VDC written as a virtual device context instead.",
                scanned_address or record.name, record.name, context.name,
            )
            return False
        if not candidates:
            log.warning(
                "%s: %s -- that chassis is not in NetBox, and a VDC is not created as a "
                "device of its own. Scan the chassis (its default VDC) first. Nothing written.",
                scanned_address or record.name, context.detail,
            )
            return True
        chassis = candidates[0]
        if len(candidates) > 1:
            log.warning("%s: serial %s is on %d devices; using %s as the chassis",
                        scanned_address or record.name, record.serial, len(candidates),
                        chassis.get("name"))

        vdc = self._ensure_vdc(chassis, record, tenant_id)
        if vdc is None:
            return True
        if self.options.sync_interfaces:
            self._sync_interfaces(chassis, record, scanned_address, tenant_id, vdc=vdc)
        created = [(record, chassis)]
        if self.options.sync_cables and self.options.sync_interfaces:
            self.cables.sync_result(result, created)
        if self.options.sync_fhrp_groups and self.options.sync_interfaces and result.fhrp_groups:
            self._sync_fhrp_groups(result, created)
        return True

    def _ensure_vdc(self, chassis: dict, record: DeviceRecord,
                    tenant_id: int | None) -> dict | None:
        context = record.context
        name = context.name or record.name
        description = "Hostname %s" % record.name
        payload = {"device": chassis["id"], "name": name, "status": "active",
                   "description": description}
        if context.identifier is not None:
            payload["identifier"] = context.identifier
        if tenant_id:
            payload["tenant"] = tenant_id
        vdc = self.netbox.ensure(
            VDC_ENDPOINT, {"device_id": chassis["id"], "name": name}, payload,
            label=f"virtual device context {name} on {chassis.get('name')}",
        )
        if vdc is None or vdc.get("id", 0) < 0:
            return vdc
        return self.netbox.ensure_fields(
            VDC_ENDPOINT, vdc,
            {"identifier": context.identifier, "description": description},
            label=f"virtual device context {name}",
        )

    def _ensure_interface_vdc(self, interface: dict, vdc: dict) -> None:
        """Allocate a chassis port to the VDC that reported it.

        A port can sit in more than one VDC -- mgmt0 is in all of them -- so
        this adds to the allocation rather than replacing it.
        """
        if interface.get("id", 0) < 0 or vdc.get("id", 0) < 0:
            return
        current = [v.get("id") if isinstance(v, dict) else v
                   for v in (interface.get("vdcs") or [])]
        if vdc["id"] in current:
            return
        updated = self.netbox.update(
            "/dcim/interfaces/", interface["id"], {"vdcs": current + [vdc["id"]]},
            label=f"interface {interface.get('name')} in VDC {vdc.get('name')}",
        )
        if updated:
            interface.update(updated)

    def _link_vcmp_host(self, device: dict, record: DeviceRecord) -> None:
        """Point a vCMP guest at the chassis it runs on.

        The host is found by the chassis serial the guest reported, which is
        deliberately not written to the guest. A guest recorded before guests
        were understood may carry that serial; it is moved off, or the host's
        own scan would collide with the guest on every sweep. A host not yet
        in NetBox is not an error: the link is made on a later sweep.
        """
        context = record.context
        if device.get("id", 0) < 0:
            log.info("[dry-run] would link %s to its vCMP host (chassis serial %s)",
                     record.name, context.chassis_serial or "?")
            return
        serial = (device.get("serial") or "").strip()
        if serial and context.chassis_serial and serial.lower() == context.chassis_serial.lower():
            log.warning("%s: carried its vCMP host's chassis serial %s; moving it off the guest",
                        record.name, serial)
            updated = self.netbox.update(DEVICES_ENDPOINT, device["id"], {"serial": ""},
                                         label=f"device {record.name} serial")
            if updated:
                device.update(updated)
        host = self._vcmp_host_for(context.chassis_serial, exclude_id=device["id"])
        if host is None:
            log.info("%s: vCMP host with chassis serial %s is not in NetBox yet; "
                     "the link is made on a later sweep", record.name,
                     context.chassis_serial or "?")
            return
        self._ensure_vcmp_host_field()
        current = (device.get("custom_fields") or {}).get(VCMP_HOST_FIELD)
        current_id = current.get("id") if isinstance(current, dict) else current
        if current_id == host["id"]:
            return
        self.netbox.update(
            DEVICES_ENDPOINT, device["id"], {"custom_fields": {VCMP_HOST_FIELD: host["id"]}},
            label=f"device {record.name} vCMP host {host.get('name')}",
        )

    def _vcmp_host_for(self, chassis_serial: str, exclude_id: int) -> dict | None:
        if not chassis_serial:
            return None
        candidates = [d for d in self._devices_with_serial(chassis_serial)
                      if d.get("id") != exclude_id]
        # Prefer a record that is not itself a guest: one still carrying the
        # host's serial from before guests were understood would link a guest
        # to a guest.
        hosts = [d for d in candidates
                 if not (d.get("custom_fields") or {}).get(VCMP_HOST_FIELD)]
        return (hosts or candidates)[0] if candidates else None

    def _ensure_vcmp_host_field(self) -> None:
        if self._vcmp_field_ready:
            return
        spec = dict(VCMP_HOST_CUSTOM_FIELD)
        self.netbox.ensure_custom_field(
            spec.pop("name"), spec.pop("object_types"), field_type=spec.pop("type"),
            label=spec.pop("label"), description=spec.pop("description"), **spec,
        )
        self._vcmp_field_ready = True

    def _patch_device(self, existing: dict, desired: dict, record: DeviceRecord) -> dict:
        # custom_fields merges rather than replaces, so send only our key and
        # leave any other custom fields on the device untouched.
        if "custom_fields" in desired:
            current = (existing.get("custom_fields") or {}).get(SOFTWARE_VERSION_FIELD)
            wanted = desired["custom_fields"][SOFTWARE_VERSION_FIELD]
            if current == wanted:
                desired.pop("custom_fields")
        return self.netbox.ensure_fields(
            "/dcim/devices/", existing, desired, label=f"device {record.name}"
        )

    def _apply_site_move(self, existing: dict, site_id: int, desired: dict,
                         record: DeviceRecord) -> None:
        """Move a device whose NetBox site disagrees with the scan.

        A device is matched by serial first, and a serial is site-independent —
        so a unit that was racked somewhere else is found and then quietly left
        at its old site. For a stack that is worse than untidy: the members
        scanned for the first time land at the new site while the one already in
        NetBox stays behind, and the virtual chassis ends up spanning two sites.
        """
        current = existing.get("site") or {}
        if not current or current.get("id") == site_id:
            return
        if not self.options.move_devices_between_sites:
            log.warning(
                "%s is at site %r in NetBox but was scanned from a %s address — "
                "left where it is (move_devices_between_sites is off)",
                record.name, current.get("name"), "different site",
            )
            return
        log.warning(
            "%s moved from site %r to the site its address belongs to",
            record.name, current.get("name"),
        )
        desired["site"] = site_id

    def _log_model_correction(self, existing: dict, device_type: dict | None,
                              record: DeviceRecord) -> None:
        if device_type is None:
            return
        current = (existing.get("device_type") or {}).get("model")
        if current and current != device_type.get("model"):
            log.info(
                "%s: device type corrected %r -> %r (reported by the device itself)",
                record.name, current, device_type.get("model"),
            )

    def _set_master(self, virtual_chassis: dict | None, created: list) -> None:
        if virtual_chassis is None:
            return
        for record, device in created:
            if record.vc_is_master and device is not None:
                current = (virtual_chassis.get("master") or {}).get("id")
                if current != device["id"]:
                    self.netbox.update(
                        "/dcim/virtual-chassis/", virtual_chassis["id"],
                        {"master": device["id"]},
                        label=f"virtual chassis {virtual_chassis.get('name')} master",
                    )
                return

    # --- supporting objects -------------------------------------------------

    def _ensure_manufacturer(self, name: str) -> dict | None:
        if not name:
            return None
        return self.netbox.ensure(
            "/dcim/manufacturers/",
            {"name": name},
            {"name": name, "slug": slugify(name)},
            label=f"manufacturer {name}",
        )

    def _ensure_device_type(self, manufacturer: dict | None, model: str,
                            part_number: str = "") -> dict | None:
        """Create the device type exactly as the device named its model.

        The model string is used verbatim. Rewriting it — stripping a prefix,
        title-casing it, gluing the manufacturer on — is how you end up with
        `aristaDCS7050SX272Q` instead of `DCS-7050SX-72Q`.

        part_number is the vendor's own orderable identifier when the device
        publishes one apart from the name (Juniper's FRU model name). It is
        written on create and filled in on an existing type that has none;
        a part number somebody typed by hand is never overwritten.
        """
        if not model or manufacturer is None:
            return None
        existing = self.netbox.first(
            "/dcim/device-types/", {"manufacturer_id": manufacturer["id"], "model": model}
        )
        if existing is not None:
            if part_number and not (existing.get("part_number") or "").strip():
                updated = self.netbox.update(
                    "/dcim/device-types/", existing["id"], {"part_number": part_number},
                    label=f"device type {model} part number",
                )
                return updated or existing
            return existing
        payload = {"manufacturer": manufacturer["id"], "model": model, "slug": slugify(model)}
        if part_number:
            payload["part_number"] = part_number
        return self.netbox.create("/dcim/device-types/", payload, label=f"device type {model}")

    def _ensure_module_type(self, manufacturer: dict | None, model: str) -> dict | None:
        if not model or manufacturer is None:
            return None
        return self.netbox.ensure(
            "/dcim/module-types/",
            {"manufacturer_id": manufacturer["id"], "model": model},
            {"manufacturer": manufacturer["id"], "model": model},
            label=f"module type {model}",
        )

    def _ensure_role(self, name: str) -> dict | None:
        if not name:
            return None
        return self.netbox.ensure(
            "/dcim/device-roles/",
            {"slug": slugify(name)},
            {"name": name.replace("-", " ").title(), "slug": slugify(name)},
            label=f"device role {name}",
        )

    def _ensure_platform(self, name: str, manufacturer: dict | None) -> dict | None:
        if not name:
            return None
        payload = {"name": name, "slug": slugify(name)}
        if manufacturer is not None:
            payload["manufacturer"] = manufacturer["id"]
        return self.netbox.ensure(
            "/dcim/platforms/", {"slug": slugify(name)}, payload, label=f"platform {name}"
        )

    # --- software version -----------------------------------------------

    def _lifecycle_available(self) -> bool:
        """Can this NetBox actually take a software report?

        The endpoint is probed rather than the plugin merely being listed: an
        instance can run a version of the Lifecycle plugin that predates the
        software models, and the difference between "installed" and "has the
        endpoint" is the difference between reporting versions and getting an
        HTML 404 back for every device.
        """
        if self._use_lifecycle is None:
            self._use_lifecycle = (
                self.netbox.plugin_installed(LIFECYCLE_PLUGIN)
                and self.netbox.endpoint_available(LIFECYCLE_ENDPOINT)
            )
            log.info(
                "software versions will be recorded in %s",
                "the Lifecycle plugin" if self._use_lifecycle
                else f"the '{SOFTWARE_VERSION_FIELD}' custom field",
            )
        return self._use_lifecycle

    def _queue_software_report(self, device: dict | None, record: DeviceRecord,
                               result: ScanResult) -> None:
        """Record a device's running version for the end-of-run batch.

        Only when the Lifecycle plugin is present — otherwise the version was
        already written to the custom field as part of the device payload.
        """
        if not self._lifecycle_available():
            return
        if device is None or not record.software_version:
            return
        if device.get("id", 0) < 0:
            # Dry-run placeholder: there is no device to report against.
            log.info("[dry-run] would report %s running %s to the Lifecycle plugin",
                     record.name, record.software_version)
            return
        report = {
            "device": device["id"],
            # Raw, exactly as the device reported it. Never normalised, padded
            # or zero-filled here: comparing versions is the plugin's job, and
            # pre-massaging the string would mean a parsing bug on this side
            # could not be corrected later without a full rescan.
            "version": record.software_version,
            "source": LIFECYCLE_SOURCE_SNMP,
        }
        if record.platform:
            report["platform"] = record.platform
        facts_time = result.facts.collected_at if result.facts else None
        if facts_time is not None:
            # When the device was walked, not when this batch is sent. A fleet
            # sweep takes time and may be pushed later still; without this the
            # plugin stamps receipt time and a stale reading renders as fresh.
            report["collected_at"] = facts_time.isoformat()
        # The verbatim string the version was read out of, so a version that
        # looks wrong can be traced to what the device actually said rather
        # than argued about.
        facts = result.facts
        if facts is not None and facts.sys_descr:
            report["raw"] = facts.sys_descr[:2000]
        self._software_reports.append(report)

    def flush_software_reports(self) -> None:
        """Send the batched version readings. Call once at the end of a run."""
        if not self._software_reports:
            return
        batch, self._software_reports = self._software_reports, []
        try:
            response = self.netbox.post_raw(
                LIFECYCLE_REPORT_ENDPOINT, batch, label="device software report"
            )
        except NetBoxError as exc:
            # The inventory itself is already written and correct; losing the
            # version reading is not worth failing the run over.
            log.warning("could not report software versions to the Lifecycle plugin: %s", exc)
            return
        if response:
            summary = response.get("summary", {})
            log.info("reported %d software versions: %s", len(batch), summary or "no summary")
            for entry in response.get("results", []):
                if entry.get("result") == "error":
                    log.warning("  %s: %s", entry.get("device"), entry.get("detail"))

    def _ensure_software_version_field(self) -> None:
        if self._custom_field_ready:
            return
        self.netbox.ensure_custom_field(
            SOFTWARE_VERSION_CUSTOM_FIELD["name"],
            SOFTWARE_VERSION_CUSTOM_FIELD["object_types"],
            field_type=SOFTWARE_VERSION_CUSTOM_FIELD["type"],
            label=SOFTWARE_VERSION_CUSTOM_FIELD["label"],
            description=SOFTWARE_VERSION_CUSTOM_FIELD["description"],
        )
        self._custom_field_ready = True

    # --- modules ------------------------------------------------------------

    def _sync_modules(self, device: dict | None, record: DeviceRecord) -> None:
        if device is None or not record.modules:
            return
        for module in record.modules:
            manufacturer = self._ensure_manufacturer(module.manufacturer or record.manufacturer)
            module_type = self._ensure_module_type(manufacturer, module.model)
            if module_type is None:
                continue
            bay_name = _fit(module.bay_name, MODULE_BAY_NAME_LENGTH)
            payload = {"device": device["id"], "name": bay_name}
            if bay_name != module.bay_name:
                # The full name is worth keeping — it is what the device
                # called the slot, and it is how somebody matches this row
                # against the output of `show inventory`.
                payload["description"] = module.bay_name[:MODULE_BAY_DESCRIPTION_LENGTH]
            bay = self.netbox.ensure(
                "/dcim/module-bays/",
                {"device_id": device["id"], "name": bay_name},
                payload,
                label=f"module bay {bay_name} on {record.name}",
            )
            if bay is None:
                continue
            existing = self.netbox.first("/dcim/modules/", {"module_bay_id": bay["id"]})
            desired = {"module_type": module_type["id"]}
            if module.serial:
                desired["serial"] = module.serial
            if existing is not None:
                self._note_module_replacement(device, existing, module, record)
                self.netbox.ensure_fields(
                    "/dcim/modules/", existing, desired,
                    label=f"module in {bay_name} on {record.name}",
                )
                continue
            self.netbox.create(
                "/dcim/modules/",
                {"device": device["id"], "module_bay": bay["id"], **desired},
                label=f"module {module.model} in {module.bay_name} on {record.name}",
            )

    def _note_module_replacement(self, device: dict | None, existing: dict,
                                 module: ModuleRecord, record: DeviceRecord) -> None:
        """Record a line card swap before the new serial overwrites the old.

        Unlike a chassis, the old module record cannot be kept: NetBox requires
        a module to sit in a bay, and the bay is about to hold the new part.
        So the audit row is written first and is the only place the removed
        serial survives — which is exactly why it is written at all.
        """
        if not self.options.retain_replaced_hardware:
            return
        old_serial = (existing.get("serial") or "").strip()
        new_serial = (module.serial or "").strip()
        # Case-insensitive for the same reason as the chassis path.
        if not old_serial or not new_serial or old_serial.lower() == new_serial.lower():
            return
        if device is None or device.get("id", 0) < 0:
            return

        log.warning(
            "%s bay %s: module serial changed %s -> %s — recording the swap; the "
            "removed part cannot be kept as a module row because its bay is being "
            "refilled",
            record.name, module.bay_name, old_serial, new_serial,
        )
        self._record_replacement(
            kind="module", device_id=device["id"], replaced_device_id=None,
            old_serial=old_serial, new_serial=new_serial, model=module.model,
            module_bay=module.bay_name,
        )

    # --- interfaces and addresses -------------------------------------------

    # Interface names that carry the management address on the platforms this
    # scanner supports, lowercased. Used only to place an address the device
    # answered on but did not list — see _ensure_primary_ip.
    MANAGEMENT_INTERFACE_NAMES = (
        "management1", "management0", "ma1",            # Arista
        "gigabitethernet0/0", "fastethernet0",          # Cisco OOB
        "fxp0", "me0", "vme",                           # Juniper
        "mgmt", "management", "mgmt0", "mgt",           # F5, Palo Alto, Fortinet
        "eth0", "eth1",                                 # appliances
    )

    def _sync_interfaces(self, device: dict | None, record: DeviceRecord,
                         scanned_address: str, tenant_id: int | None = None,
                         vdc: dict | None = None) -> None:
        """Write a record's interfaces onto `device`.

        `vdc` is set when the record is a Nexus VDC and `device` its chassis:
        the ports are the chassis's and are allocated to the context, and the
        polled address becomes the context's primary IP rather than the
        chassis's.
        """
        if device is None or not record.interfaces:
            return

        existing_by_name = {
            iface["name"]: iface
            for iface in self.netbox.all("/dcim/interfaces/", {"device_id": device["id"]})
        }

        for interface in record.interfaces:
            netbox_interface = self._ensure_interface(device, interface, existing_by_name)
            if netbox_interface is None:
                continue
            if vdc is not None:
                self._ensure_interface_vdc(netbox_interface, vdc)
            if interface.mac_address:
                self._ensure_mac(netbox_interface, interface.mac_address)
            if self.options.sync_ips:
                for cidr in interface.ip_addresses:
                    try:
                        self._ensure_ip(device, netbox_interface, cidr, scanned_address,
                                        tenant_id, vdc=vdc)
                    except NetBoxError as exc:
                        # One address NetBox will not take must not cost the
                        # rest of the device. This used to abort the whole
                        # sync partway, so every interface after the offending
                        # one silently went missing and the device looked
                        # half-scanned for a reason nothing explained.
                        log.warning(
                            "%s on %s: %s — skipped, continuing with the device",
                            cidr, interface.name, exc,
                        )

        if self.options.set_primary_ip and scanned_address:
            self._ensure_primary_ip(device, scanned_address, tenant_id, vdc=vdc)

    def _ensure_primary_ip(self, device: dict, scanned_address: str,
                           tenant_id: int | None = None, vdc: dict | None = None) -> None:
        """Make the address we polled the primary IP. Always.

        The rule is flat: the primary IP is the address the device was
        onboarded with. That is the address an operator reaches it on and the
        one the scan targeted, so there is nothing to infer about *which*
        address belongs in the field.

        What does need deciding is where to hang it, because NetBox refuses a
        primary that is not assigned to one of the device's interfaces. In
        order of how much is actually known:

          1. the device reported it on an interface — handled during the
             interface sync, and it keeps the real mask;
          2. an address object already sits on one of this device's
             interfaces — no inference, and again the recorded mask stands;
          3. a management-named interface exists — a decent guess at where a
             management address lives, but only a guess;
          4. nothing fits, so a virtual interface is created to hold it.

        Step 4 exists because the alternative was leaving the field blank, and
        the rule says otherwise. It creates an interface rather than attaching
        the address to whichever data port happened to come first: claiming
        Ethernet1 carries an address it does not carry is a false statement
        about real hardware, where a virtual interface labelled as holding the
        polled address is at worst an extra row that says exactly what it is.
        """
        # For a VDC the address belongs to the context, not to the chassis
        # whose interfaces carry it.
        owner, owner_path = (vdc, VDC_ENDPOINT) if vdc is not None else (device, DEVICES_ENDPOINT)
        if device.get("id", 0) < 0 or owner.get("id", 0) < 0:
            # A dry-run placeholder. Negative ids exist only in this process,
            # so reading one back is a guaranteed 404 — and there is nothing
            # to write either. Say what would happen and stop.
            log.info("would set %s as the primary IP of %s",
                     scanned_address, owner.get("name"))
            return

        # Refetched because the interface loop may have set it a moment ago,
        # and the dict we were handed predates that.
        fresh_owner = self.netbox.get(f"{owner_path}{owner['id']}/")
        if (fresh_owner.get("primary_ip4") or {}).get("id"):
            return          # step 1: the device reported it
        fresh = fresh_owner if vdc is None else self.netbox.get(f"{DEVICES_ENDPOINT}{device['id']}/")

        interfaces = self.netbox.all("/dcim/interfaces/", {"device_id": device["id"]})

        # Step 2. An existing address object already on this device wins
        # outright: nothing is inferred and the recorded mask is preserved.
        # Queried without a mask, which matches this host at whatever prefix
        # length it was recorded with. That matters twice over: NetBox's
        # duplicate rule is on the host address rather than the CIDR, so
        # creating <addr>/32 beside an existing <addr>/24 is refused as a
        # duplicate -- and a lookup by CIDR would never have found the /24 to
        # know that.
        candidates = self.netbox.all("/ipam/ip-addresses/",
                                     {"address": scanned_address, **self._in_vrf()})

        by_id = {iface["id"] for iface in interfaces}
        mine = next((c for c in candidates
                     if c.get("assigned_object_id") in by_id), None)
        if mine is not None:
            self._set_primary_ip(fresh_owner, mine, owner_path)
            return

        chosen = next(
            (i for i in interfaces
             if (i.get("name") or "").lower() in self.MANAGEMENT_INTERFACE_NAMES),
            None,
        )
        if chosen is not None:
            # Step 3.
            log.info(
                "%s did not report %s on any interface — recording it on %s",
                device.get("name"), scanned_address, chosen.get("name"),
            )
        else:
            # Step 4.
            chosen = self._ensure_holding_interface(fresh, scanned_address)
            if chosen is None:
                return

        # An address already in IPAM keeps the mask somebody recorded for it;
        # only a genuinely new one becomes a /32, because the device never told
        # us its mask and inventing one would put a wrong prefix into IPAM.
        # Handing the existing CIDR to _ensure_ip also reuses its rules: adopt
        # an unassigned address, refuse to steal one belonging to another
        # device.
        cidr = candidates[0]["address"] if candidates else f"{scanned_address}/32"
        try:
            self._ensure_ip(fresh, chosen, cidr, scanned_address, tenant_id, vdc=vdc)
        except NetBoxError as exc:
            log.warning("could not record %s on %s: %s",
                        scanned_address, chosen.get("name"), exc)

    def _ensure_holding_interface(self, device: dict, scanned_address: str) -> dict | None:
        """A virtual interface to hang the polled address on.

        Named and described so nobody mistakes it for something the device
        reported. Idempotent: rescans find it rather than making another.
        """
        existing = self.netbox.first("/dcim/interfaces/", {
            "device_id": device["id"], "name": PRIMARY_IP_INTERFACE_NAME,
        })
        if existing is not None:
            return existing
        log.info(
            "%s did not report %s and has no management interface — creating "
            "%s to hold it, so the address it was onboarded with is still its "
            "primary IP", device.get("name"), scanned_address,
            PRIMARY_IP_INTERFACE_NAME,
        )
        try:
            return self.netbox.create("/dcim/interfaces/", {
                "device": device["id"],
                "name": PRIMARY_IP_INTERFACE_NAME,
                "type": "virtual",
                "description": (
                    "Holds the address this device was discovered on. Created "
                    "by the SNMP inventory because the device reported no "
                    "interface carrying it."
                ),
            }, label=f"interface {PRIMARY_IP_INTERFACE_NAME} on {device.get('name')}")
        except NetBoxError as exc:
            log.warning("could not create %s on %s: %s",
                        PRIMARY_IP_INTERFACE_NAME, device.get("name"), exc)
            return None

    def _ensure_interface(self, device: dict, interface: InterfaceRecord,
                          existing_by_name: dict) -> dict | None:
        desired = {
            "type": interface.type_slug,
            "enabled": interface.enabled,
            "description": interface.description,
            "mtu": interface.mtu,
            "speed": interface.speed_kbps,
        }
        existing = existing_by_name.get(interface.name)
        if existing is not None:
            # `enabled` is a boolean, so False is a real value rather than
            # "unknown"; ensure_fields skips falsey values, so handle it here.
            if existing.get("enabled") != interface.enabled:
                self.netbox.update(
                    "/dcim/interfaces/", existing["id"], {"enabled": interface.enabled},
                    label=f"interface {interface.name} enabled",
                )
                existing["enabled"] = interface.enabled
            return self.netbox.ensure_fields(
                "/dcim/interfaces/", existing, desired,
                label=f"interface {interface.name}",
            )
        payload = {"device": device["id"], "name": interface.name, "type": interface.type_slug,
                   "enabled": interface.enabled}
        payload.update({k: v for k, v in desired.items() if v not in (None, "") and k != "type"})
        return self.netbox.create(
            "/dcim/interfaces/", payload, label=f"interface {interface.name} on {device.get('name')}"
        )

    def _ensure_mac(self, interface: dict | None, mac: str) -> None:
        """Create the MAC object and point the interface at it.

        NetBox 4.x moved MACs into their own model; `interface.mac_address` is
        read-only and derived from `primary_mac_address`. Duplicate POSTs to
        /dcim/mac-addresses/ are not deduplicated by NetBox, so the lookup
        first is mandatory or every rescan adds another MACAddress row.
        """
        if interface is None:
            return
        existing = self.netbox.first(
            "/dcim/mac-addresses/", {"mac_address": mac, "interface_id": interface["id"]}
        )
        if existing is None:
            existing = self.netbox.create(
                "/dcim/mac-addresses/",
                {
                    "mac_address": mac,
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": interface["id"],
                },
                label=f"mac {mac}",
            )
        if existing is None:
            return
        if (interface.get("primary_mac_address") or {}).get("id") != existing["id"]:
            self.netbox.update(
                "/dcim/interfaces/", interface["id"],
                {"primary_mac_address": existing["id"]},
                label=f"interface {interface.get('name')} primary MAC",
            )

    def _ensure_ip(self, device: dict, interface: dict | None, cidr: str,
                   scanned_address: str, tenant_id: int | None = None,
                   vdc: dict | None = None) -> None:
        if interface is None:
            return
        existing = self.netbox.first(
            "/ipam/ip-addresses/",
            {"address": cidr, "interface_id": interface["id"]},
        )
        if (existing is not None and self._vrf_id
                and (existing.get("vrf") or {}).get("id") != self._vrf_id):
            # Already on this interface but in another table -- written before
            # the scanner knew which VRF the device lives in. Moved rather
            # than duplicated. Only ever INTO a VRF: with no VRF to go on, an
            # address somebody placed in one by hand is left where it is.
            existing = self.netbox.update(
                "/ipam/ip-addresses/", existing["id"], {"vrf": self._vrf_id},
                label=f"ip {cidr} into its VRF",
            ) or existing
        if existing is None:
            # The address may already exist unassigned — imported from the CSV
            # before the device was ever scanned. Adopt it rather than making a
            # duplicate. Looked for in this host's routing table only: the
            # same address in another VRF belongs to another network.
            candidates = self.netbox.all("/ipam/ip-addresses/",
                                         {"address": cidr, **self._in_vrf()})
            unassigned = [c for c in candidates if not c.get("assigned_object_id")]
            if unassigned:
                existing = self.netbox.update(
                    "/ipam/ip-addresses/", unassigned[0]["id"],
                    {
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": interface["id"],
                    },
                    label=f"ip {cidr} -> {interface.get('name')}",
                ) or unassigned[0]
            elif candidates:
                # Assigned to something else. Stealing it would silently break
                # whatever holds it, so leave it and say so.
                log.warning(
                    "%s already assigned elsewhere in NetBox — not reassigning to %s",
                    cidr, interface.get("name"),
                )
                return
            else:
                payload = {
                    "address": cidr,
                    "assigned_object_type": "dcim.interface",
                    "assigned_object_id": interface["id"],
                }
                if self._vrf_id:
                    payload["vrf"] = self._vrf_id
                if tenant_id:
                    payload["tenant"] = tenant_id
                existing = self.netbox.create(
                    "/ipam/ip-addresses/", payload, label=f"ip {cidr}"
                )

        if (self.options.set_primary_ip and existing is not None and scanned_address
                and cidr.split("/")[0] == scanned_address):
            if vdc is not None:
                self._set_primary_ip(vdc, existing, VDC_ENDPOINT)
            else:
                self._set_primary_ip(device, existing)

    def _set_primary_ip(self, owner: dict, ip: dict, path: str = DEVICES_ENDPOINT) -> None:
        """Make the address we actually polled the primary IP of `owner` --
        a device, or a virtual device context on one."""
        if (owner.get("primary_ip4") or {}).get("id") == ip["id"]:
            return
        if ":" in ip.get("address", ""):
            return
        if owner.get("id", 0) < 0:
            return
        try:
            updated = self.netbox.update(
                path, owner["id"], {"primary_ip4": ip["id"]},
                label=f"{owner.get('name')} primary IP",
            )
            # Remembered on the dict in hand, so the interface loop and the
            # fallback that follows it do not both write it.
            owner["primary_ip4"] = (updated or {}).get("primary_ip4") or {"id": ip["id"]}
        except NetBoxError as exc:
            # Not fatal: the inventory is still correct without a primary IP.
            log.warning("could not set primary IP on %s: %s", owner.get("name"), exc)

    # --- access points ------------------------------------------------------

    def _sync_access_points(self, result: ScanResult, site_id: int,
                            tenant_id: int | None = None) -> None:
        """Create a Device for each AP the controller reported.

        APs are inventoried from their controller because they are rarely
        reachable from a poller themselves — they tunnel to the controller and
        often live on management networks the poller has no route to.
        """
        for record in result.access_points:
            device = self._ensure_device(record, site_id, virtual_chassis=None,
                                         tenant_id=tenant_id, scanned_address="")
            # APs carry a software version like any other device (read from the
            # controller's AP table, or inherited from the controller), but the
            # main loop only reports versions for result.devices — without this
            # the Lifecycle plugin never hears about APs at all and their
            # version stays blank whenever the plugin is installed.
            if self.options.manage_software_version:
                self._queue_software_report(device, record, result)


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """NetBox slug: lowercase, non-alphanumerics collapsed to single hyphens."""
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")
    return slug[:100] or "unknown"
