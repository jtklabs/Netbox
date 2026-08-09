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

from .model import DeviceRecord, InterfaceRecord, ModuleRecord, ScanResult
from .netbox import NetBox, NetBoxError

log = logging.getLogger(__name__)

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


class Syncer:
    def __init__(self, netbox: NetBox, options: SyncOptions | None = None):
        self.netbox = netbox
        self.options = options or SyncOptions()
        self._custom_field_ready = False
        self._use_lifecycle: bool | None = None
        # Version readings are batched and sent once at the end of a run. The
        # ingest endpoint takes a list, and one call for a fleet beats one call
        # per device across a WAN.
        self._software_reports: list[dict] = []

    # --- entry point --------------------------------------------------------

    def sync(self, result: ScanResult, site_id: int | None, scanned_address: str = "") -> None:
        """Write one scanned host — a single device or a whole stack."""
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
            device = self._ensure_device(record, site_id, virtual_chassis)
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
                self._sync_interfaces(device, record, scanned_address)
            if self.options.manage_software_version:
                self._queue_software_report(device, record, result)

        if self.options.sync_access_points and result.access_points:
            self._sync_access_points(result, site_id)

    # --- devices ------------------------------------------------------------

    def _ensure_device(self, record: DeviceRecord, site_id: int,
                       virtual_chassis: dict | None) -> dict | None:
        manufacturer = self._ensure_manufacturer(record.manufacturer)
        device_type = self._ensure_device_type(manufacturer, record.model)
        role = self._ensure_role(
            self.options.access_point_role if record.is_access_point else self.options.device_role
        )
        platform = self._ensure_platform(record.platform, manufacturer)

        existing = self._find_device(record, site_id)

        desired: dict = {}
        if record.serial:
            desired["serial"] = record.serial
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
        return self.netbox.create("/dcim/devices/", payload, label=f"device {record.name}")

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
            bay = self.netbox.ensure(
                "/dcim/module-bays/",
                {"device_id": device["id"], "name": module.bay_name},
                {"device": device["id"], "name": module.bay_name},
                label=f"module bay {module.bay_name} on {record.name}",
            )
            if bay is None:
                continue
            existing = self.netbox.first("/dcim/modules/", {"module_bay_id": bay["id"]})
            desired = {"module_type": module_type["id"]}
            if module.serial:
                desired["serial"] = module.serial
            if existing is not None:
                self.netbox.ensure_fields(
                    "/dcim/modules/", existing, desired,
                    label=f"module in {module.bay_name} on {record.name}",
                )
                continue
            self.netbox.create(
                "/dcim/modules/",
                {"device": device["id"], "module_bay": bay["id"], **desired},
                label=f"module {module.model} in {module.bay_name} on {record.name}",
            )

    # --- interfaces and addresses -------------------------------------------

    def _sync_interfaces(self, device: dict | None, record: DeviceRecord,
                         scanned_address: str) -> None:
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
                    self._ensure_ip(device, netbox_interface, cidr, scanned_address)

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
                   scanned_address: str) -> None:
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
                existing = self.netbox.create(
                    "/ipam/ip-addresses/",
                    {
                        "address": cidr,
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": interface["id"],
                    },
                    label=f"ip {cidr}",
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

    def _sync_access_points(self, result: ScanResult, site_id: int) -> None:
        """Create a Device for each AP the controller reported.

        APs are inventoried from their controller because they are rarely
        reachable from a poller themselves — they tunnel to the controller and
        often live on management networks the poller has no route to.
        """
        for record in result.access_points:
            self._ensure_device(record, site_id, virtual_chassis=None)


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """NetBox slug: lowercase, non-alphanumerics collapsed to single hyphens."""
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")
    return slug[:100] or "unknown"
