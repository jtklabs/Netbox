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

from .model import DeviceRecord, InterfaceRecord, ModuleRecord, ScanResult
from .netbox import NetBox, NetBoxError

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

REPLACEMENT_ENDPOINT = "/plugins/discovery/hardware-replacements/"
ISSUE_ENDPOINT = "/plugins/discovery/issues/"


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


class Syncer:
    def __init__(self, netbox: NetBox, options: SyncOptions | None = None):
        self.netbox = netbox
        self.options = options or SyncOptions()
        self._custom_field_ready = False
        self._use_lifecycle: bool | None = None
        self._replacements_ok: bool | None = None
        self._issues_ok: bool | None = None
        # Chassis swaps are detected before the replacement device exists, so
        # the audit row waits here until it has something to point at.
        self._pending_replacements: list[dict] = []
        # Version readings are batched and sent once at the end of a run. The
        # ingest endpoint takes a list, and one call for a fleet beats one call
        # per device across a WAN.
        self._software_reports: list[dict] = []

    # --- entry point --------------------------------------------------------

    def sync(self, result: ScanResult, site_id: int | None, scanned_address: str = "",
             tenant_id: int | None = None) -> None:
        """Write one scanned host — a single device or a whole stack.

        `tenant_id` files the result against the company that owns it. It
        matters most where address space overlaps between us and companies we
        have bought: the tenant is what made the address resolvable in the
        first place, and dropping it here would leave the device unattributed.
        """
        if not result.devices:
            log.warning("%s: nothing to sync (device reported no chassis)", result.host)
            return
        if site_id is None:
            log.warning("%s: no site could be determined, skipping", result.host)
            return

        if self.options.manage_software_version and not self._lifecycle_available():
            self._ensure_software_version_field()

        virtual_chassis = None
        if result.is_stack:
            virtual_chassis = self.netbox.ensure(
                "/dcim/virtual-chassis/",
                {"name": result.virtual_chassis_name},
                {"name": result.virtual_chassis_name},
                label=f"virtual chassis {result.virtual_chassis_name}",
            )

        created: list[tuple[DeviceRecord, dict]] = []
        for record in result.devices:
            device = self._ensure_device(record, site_id, virtual_chassis, tenant_id,
                                         scanned_address=scanned_address)
            if device is not None:
                created.append((record, device))

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

        if self.options.sync_access_points and result.access_points:
            self._sync_access_points(result, site_id, tenant_id)

    # --- devices ------------------------------------------------------------

    def _ensure_device(self, record: DeviceRecord, site_id: int,
                       virtual_chassis: dict | None,
                       tenant_id: int | None = None,
                       scanned_address: str = "") -> dict | None:
        manufacturer = self._ensure_manufacturer(record.manufacturer)
        device_type = self._ensure_device_type(manufacturer, record.model)
        role = self._ensure_role(
            self.options.access_point_role if record.is_access_point else self.options.device_role
        )
        platform = self._ensure_platform(record.platform, manufacturer)

        existing = self._find_device(record, site_id)

        conflict = self._serial_belongs_to_another_device(existing, record, scanned_address)
        if conflict:
            # Refusing is the whole point. Matching on serial is what makes a
            # re-IP'd box resolve to its existing record, and it is also what
            # would let this scan write straight over a different device.
            self._raise_issue(existing, record, scanned_address, conflict)
            return None

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

        if existing is not None:
            replaced = self._handle_serial_change(existing, record, site_id)
            if replaced is not None:
                # The old record has been retired and given up the name; fall
                # through and create a fresh device for the new hardware.
                existing = None
            else:
                self._log_model_correction(existing, device_type, record)
                self._apply_site_move(existing, site_id, desired, record)
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

    def _serial_belongs_to_another_device(self, existing: dict | None,
                                          record: DeviceRecord,
                                          scanned_address: str) -> str:
        """Is this serial already held against a *different* box?

        Serial matching is deliberate and mostly right: it is what makes a
        device that was renamed, re-addressed or moved resolve to the record it
        already has. The dangerous case is when two devices carry one serial —
        a mistyped entry, a vendor reusing one, or two records that were always
        the same box. Then the match is wrong and syncing would overwrite a
        record belonging to something else, silently.

        Telling the two apart comes down to what else agrees. A rename keeps
        the address; a re-address keeps the name. When *neither* matches, there
        is no evidence these are the same device beyond a serial that is by
        assumption suspect, so it is refused.
        """
        if existing is None or not record.serial:
            return ""
        stored_serial = (existing.get("serial") or "").strip()
        if stored_serial.lower() != record.serial.strip().lower():
            # Matched by name, not serial — that is the replacement path.
            return ""

        stored_name = (existing.get("name") or "").strip().lower()
        reported_name = record.name.strip().lower()
        if stored_name and reported_name and stored_name == reported_name:
            return ""

        primary = (existing.get("primary_ip4") or existing.get("primary_ip") or {})
        primary_address = (primary.get("address") or "").split("/")[0]
        if scanned_address and primary_address and scanned_address == primary_address:
            # Same address, different name: the box was renamed. Fine.
            return ""

        return (
            "Serial %s is already on %s%s, which reports a different name and a "
            "different address. Either two devices have been given one serial, or "
            "one of them is wrong. Nothing was changed."
            % (
                record.serial,
                existing.get("name") or "device %s" % existing.get("id"),
                " (%s)" % primary_address if primary_address else "",
            )
        )

    def _raise_issue(self, existing: dict | None, record: DeviceRecord,
                     scanned_address: str, detail: str) -> None:
        """Record something a person has to settle, where they will see it."""
        log.error("%s: %s", scanned_address or record.name, detail)
        if not self._issues_available():
            return
        payload = {
            "kind": "duplicate-serial",
            "status": "open",
            "address": scanned_address or "",
            "serial": record.serial,
            "reported_name": record.name,
            "detail": detail,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        if existing is not None and existing.get("id", 0) > 0:
            payload["device"] = existing["id"]
        try:
            self.netbox.create(ISSUE_ENDPOINT, payload, label="discovery issue")
        except NetBoxError as exc:
            # A duplicate open issue is the expected collision — the sweep runs
            # four times a day and this one is already on the list.
            if "unique" in str(exc).lower() or "already exists" in str(exc).lower():
                log.debug("issue already open for %s", scanned_address)
            else:
                log.error("could not record the issue: %s", exc)

    def _issues_available(self) -> bool:
        if self._issues_ok is None:
            self._issues_ok = self.netbox.endpoint_available(ISSUE_ENDPOINT)
            if not self._issues_ok:
                log.warning(
                    "the Discovery plugin is not installed, so this could only be "
                    "logged, not raised where anyone will see it"
                )
        return self._issues_ok

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
        old_serial = (existing.get("serial") or "").strip()
        new_serial = record.serial.strip()
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

    def _find_device(self, record: DeviceRecord, site_id: int) -> dict | None:
        """Find an existing device: serial first, then name.

        Serial is the stronger key — it survives a rename, and for a stack it is
        the only thing that reliably distinguishes member 2 from member 3. Name
        is the fallback for gear that reports no serial.
        """
        if record.serial:
            found = self.netbox.first("/dcim/devices/", {"serial": record.serial})
            if found is not None:
                return found
        if record.name:
            found = self.netbox.first("/dcim/devices/", {"name": record.name, "site_id": site_id})
            if found is not None:
                return found
            # A device may have been created at the wrong site by an earlier
            # tool; match on name alone rather than making a duplicate.
            return self.netbox.first("/dcim/devices/", {"name": record.name})
        return None

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

    def _ensure_device_type(self, manufacturer: dict | None, model: str) -> dict | None:
        """Create the device type exactly as the device named its model.

        The model string is used verbatim. Rewriting it — stripping a prefix,
        title-casing it, gluing the manufacturer on — is how you end up with
        `aristaDCS7050SX272Q` instead of `DCS-7050SX-72Q`.
        """
        if not model or manufacturer is None:
            return None
        existing = self.netbox.first(
            "/dcim/device-types/", {"manufacturer_id": manufacturer["id"], "model": model}
        )
        if existing is not None:
            return existing
        return self.netbox.create(
            "/dcim/device-types/",
            {"manufacturer": manufacturer["id"], "model": model, "slug": slugify(model)},
            label=f"device type {model}",
        )

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
                         scanned_address: str, tenant_id: int | None = None) -> None:
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
            if interface.mac_address:
                self._ensure_mac(netbox_interface, interface.mac_address)
            if self.options.sync_ips:
                for cidr in interface.ip_addresses:
                    try:
                        self._ensure_ip(device, netbox_interface, cidr, scanned_address,
                                        tenant_id)
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
            self._ensure_primary_ip(device, scanned_address, tenant_id)

    def _ensure_primary_ip(self, device: dict, scanned_address: str,
                           tenant_id: int | None = None) -> None:
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
        if device.get("id", 0) < 0:
            # A dry-run placeholder. Negative ids exist only in this process,
            # so reading one back is a guaranteed 404 — and there is nothing
            # to write either. Say what would happen and stop.
            log.info("would set %s as the primary IP of %s",
                     scanned_address, device.get("name"))
            return

        # Refetched because the interface loop may have set it a moment ago,
        # and the dict we were handed predates that.
        fresh = self.netbox.get(f"/dcim/devices/{device['id']}/")
        if (fresh.get("primary_ip4") or {}).get("id"):
            return          # step 1: the device reported it

        interfaces = self.netbox.all("/dcim/interfaces/", {"device_id": device["id"]})

        # Step 2. An existing address object already on this device wins
        # outright: nothing is inferred and the recorded mask is preserved.
        # Queried without a mask, which matches this host at whatever prefix
        # length it was recorded with. That matters twice over: NetBox's
        # duplicate rule is on the host address rather than the CIDR, so
        # creating <addr>/32 beside an existing <addr>/24 is refused as a
        # duplicate -- and a lookup by CIDR would never have found the /24 to
        # know that.
        candidates = self.netbox.all("/ipam/ip-addresses/", {"address": scanned_address})

        by_id = {iface["id"] for iface in interfaces}
        mine = next((c for c in candidates
                     if c.get("assigned_object_id") in by_id), None)
        if mine is not None:
            self._set_primary_ip(fresh, mine)
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
            self._ensure_ip(fresh, chosen, cidr, scanned_address, tenant_id)
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
                   scanned_address: str, tenant_id: int | None = None) -> None:
        if interface is None:
            return
        existing = self.netbox.first(
            "/ipam/ip-addresses/",
            {"address": cidr, "interface_id": interface["id"]},
        )
        if existing is None:
            # The address may already exist unassigned — imported from the CSV
            # before the device was ever scanned. Adopt it rather than making a
            # duplicate.
            candidates = self.netbox.all("/ipam/ip-addresses/", {"address": cidr})
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
                if tenant_id:
                    payload["tenant"] = tenant_id
                existing = self.netbox.create(
                    "/ipam/ip-addresses/", payload, label=f"ip {cidr}"
                )

        if (self.options.set_primary_ip and existing is not None and scanned_address
                and cidr.split("/")[0] == scanned_address):
            self._set_primary_ip(device, existing)

    def _set_primary_ip(self, device: dict, ip: dict) -> None:
        """Make the address we actually polled the device's primary IP."""
        if (device.get("primary_ip4") or {}).get("id") == ip["id"]:
            return
        if ":" in ip.get("address", ""):
            return
        try:
            self.netbox.update(
                "/dcim/devices/", device["id"], {"primary_ip4": ip["id"]},
                label=f"device {device.get('name')} primary IP",
            )
        except NetBoxError as exc:
            # Not fatal: the inventory is still correct without a primary IP.
            log.warning("could not set primary IP on %s: %s", device.get("name"), exc)

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
