"""Per-vendor OIDs and the software-version extraction that goes with them.

Every OID in this file was resolved from the vendor's own published MIB with a
parser, not recalled — see docs/OID-SOURCES.md for the MIB and the object name
behind each constant. That matters more than usual here: the reason this
scanner exists is that the tool it replaces guessed hardware facts instead of
reading them, and a mis-transcribed OID would be the same class of bug.

Two things are collected per vendor beyond the generic MIBs:

  software version   No standard MIB carries it. Most vendors expose it as a
                     scalar in their enterprise tree; the rest only put it in
                     sysDescr, so each profile gets a regex as a fallback.

  extras             Serial and model for appliances that leave ENTITY-MIB
                     empty. Firewalls and load balancers commonly do — a PA-3220
                     or a BIG-IP answers entPhysicalTable with nothing useful,
                     so without these the device would arrive with no serial.

A profile is selected by the sysObjectID enterprise arc. Selection never
depends on the sub-arcs below the enterprise number, because those are exactly
the model-guessing lookup tables we are getting away from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Aruba WLC access-point table -------------------------------------------
#
# WLSX-WLAN-MIB wlsxWlanAPTable. INDEX is wlanAPMacAddress, so the AP's MAC is
# in the OID suffix rather than in a column of its own — column 1 is
# not-accessible and never appears in a walk.
ARUBA_AP_ENTRY = "1.3.6.1.4.1.14823.2.2.1.5.2.1.4.1"
ARUBA_AP_IP = f"{ARUBA_AP_ENTRY}.2"
ARUBA_AP_NAME = f"{ARUBA_AP_ENTRY}.3"
ARUBA_AP_GROUP = f"{ARUBA_AP_ENTRY}.4"
ARUBA_AP_SERIAL = f"{ARUBA_AP_ENTRY}.6"
ARUBA_AP_MODEL_NAME = f"{ARUBA_AP_ENTRY}.13"
ARUBA_AP_STATUS = f"{ARUBA_AP_ENTRY}.19"

ARUBA_AP_STATUS_UP = 1

# ArubaOS controller scalars (WLSX-SYSTEMEXT-MIB).
ARUBA_SYS_HOSTNAME = "1.3.6.1.4.1.14823.2.2.1.2.1.2.0"
ARUBA_SYS_MODEL_NAME = "1.3.6.1.4.1.14823.2.2.1.2.1.3.0"
ARUBA_SYS_SWITCH_ROLE = "1.3.6.1.4.1.14823.2.2.1.2.1.4.0"
ARUBA_SYS_LICENSE_SERIAL = "1.3.6.1.4.1.14823.2.2.1.2.1.11.0"


@dataclass(frozen=True)
class VendorProfile:
    """How to read one vendor's software version, serial and model."""

    name: str
    manufacturer: str
    # Platform name written to dcim.Platform — the OS family, not the version.
    platform: str = ""
    # Scalar OIDs tried in order; first non-empty answer wins.
    version_oids: tuple[str, ...] = ()
    # Separate from version_oids because several vendors publish the build
    # apart from the release, and the pair is what identifies an image: F5's
    # own ISOs are named BIGIP-<version>-<build>.iso. Joined only when both
    # come back, so a platform that reports no build reads exactly as before.
    build_oids: tuple[str, ...] = ()
    serial_oids: tuple[str, ...] = ()
    model_oids: tuple[str, ...] = ()
    # Applied to sysDescr when no version OID answered.
    version_patterns: tuple[str, ...] = ()
    # Applied to sysDescr when no model OID answered. Only for vendors that
    # publish no model scalar at all — Palo Alto and Fortinet both name the
    # model in sysDescr and nowhere else queryable. This is still the device
    # reporting its own model in its own words; it is not a sysObjectID lookup
    # table, which is the thing this scanner exists to avoid.
    model_patterns: tuple[str, ...] = ()
    # Extra subtrees to walk for this vendor (e.g. the Aruba AP table).
    extra_walks: tuple[str, ...] = ()
    # ENTITY-MIB is authoritative where it is populated. Appliances that leave
    # it empty set this so the sync layer knows a missing chassis row is normal
    # and should fall back to the scalar OIDs rather than warn.
    entity_mib_sparse: bool = False


# Generic last resort: almost every sysDescr that mentions a version writes it
# as "Version 1.2.3" or "version 1.2.3". Deliberately conservative — it must not
# match a model number, so a bare "1.2.3" with no "version" keyword is ignored.
GENERIC_VERSION_PATTERNS = (
    r"[Vv]ersion[:\s]+([0-9][\w.()\-]*)",
    r"\bv([0-9]+\.[0-9][\w.()\-]*)",
)

PROFILES: dict[int, VendorProfile] = {
    9: VendorProfile(
        name="cisco",
        manufacturer="Cisco",
        platform="Cisco IOS",
        # IOS/IOS-XE/NX-OS all report the running image in sysDescr; ENTITY-MIB
        # entPhysicalSoftwareRev is populated on some platforms and empty on
        # others, so sysDescr is the reliable one and collect.py also reads the
        # chassis entity's software rev where present.
        version_patterns=(
            r"Version\s+([0-9][\w.()\-]*)",     # IOS / IOS-XE
            r"[Ss]ystem version[:\s]+([\w.()\-]+)",
            r"version\s+([0-9][\w.()\-]*)",     # NX-OS lowercases it
        ),
    ),
    30065: VendorProfile(
        name="arista",
        manufacturer="Arista Networks",
        platform="Arista EOS",
        # "Arista Networks EOS version 4.29.2F running on an Arista Networks..."
        version_patterns=(r"EOS version\s+([\w.\-]+)",),
    ),
    14823: VendorProfile(
        name="aruba",
        manufacturer="Aruba Networks",
        platform="ArubaOS",
        serial_oids=(ARUBA_SYS_LICENSE_SERIAL,),
        model_oids=(ARUBA_SYS_MODEL_NAME,),
        # "ArubaOS (MODEL: Aruba7010), Version 8.10.0.4"
        version_patterns=(r"Version\s+([0-9][\w.\-]*)",),
        extra_walks=(ARUBA_AP_ENTRY,),
    ),
    25461: VendorProfile(
        name="paloalto",
        manufacturer="Palo Alto Networks",
        platform="PAN-OS",
        version_oids=("1.3.6.1.4.1.25461.2.1.2.1.1.0",),   # panSysSwVersion
        serial_oids=("1.3.6.1.4.1.25461.2.1.2.1.3.0",),    # panSysSerialNumber
        version_patterns=(r"PAN-OS\s+([\w.\-]+)",),
        # "Palo Alto Networks PA-3220 series firewall". PAN-COMMON-MIB has no
        # model scalar — panSysHwVersion is the hardware revision, not the PID.
        model_patterns=(r"\b(PA-[\w\-]+)", r"\b(VM-\d+)", r"\b(M-\d+)"),
        entity_mib_sparse=True,
    ),
    12356: VendorProfile(
        name="fortinet",
        manufacturer="Fortinet",
        platform="FortiOS",
        version_oids=("1.3.6.1.4.1.12356.101.4.1.1.0",),   # fgSysVersion
        serial_oids=("1.3.6.1.4.1.12356.100.1.1.1.0",),    # fnSysSerial
        version_patterns=(r"v([0-9]+\.[0-9]+\.[0-9]+)",),
        # "FortiGate-600E v7.2.8,build1639,240110 (GA)"
        model_patterns=(r"\b(Forti\w+-[\w\-]+)",),
        entity_mib_sparse=True,
    ),
    3375: VendorProfile(
        name="f5",
        manufacturer="F5 Networks",
        platform="F5 TMOS",
        version_oids=("1.3.6.1.4.1.3375.2.1.4.2.0",),      # sysProductVersion
        # sysProductBuild. The version alone is not enough to identify an
        # image: 17.1.1.3 ships in more than one build, and the hotfix level
        # is the half that says which. F5 writes the pair as
        # <version>-<build>, as in BIGIP-17.1.1.3-0.0.5.iso.
        build_oids=("1.3.6.1.4.1.3375.2.1.4.3.0",),
        serial_oids=("1.3.6.1.4.1.3375.2.1.3.3.3.0",),     # sysGeneralChassisSerialNum
        model_oids=("1.3.6.1.4.1.3375.2.1.3.5.2.0",),      # sysPlatformInfoMarketingName
        entity_mib_sparse=True,
    ),
    2620: VendorProfile(
        name="checkpoint",
        manufacturer="Check Point",
        platform="Check Point Gaia",
        version_oids=("1.3.6.1.4.1.2620.1.6.4.1.0",),      # svnVersion
        serial_oids=("1.3.6.1.4.1.2620.1.6.16.3.0",),      # svnApplianceSerialNumber
        model_oids=("1.3.6.1.4.1.2620.1.6.16.7.0",),       # svnApplianceProductName
        entity_mib_sparse=True,
    ),
    7779: VendorProfile(
        name="infoblox",
        manufacturer="Infoblox",
        platform="Infoblox NIOS",
        version_oids=("1.3.6.1.4.1.7779.3.1.1.2.1.7.0",),  # ibNiosVersion
        serial_oids=("1.3.6.1.4.1.7779.3.1.1.2.1.6.0",),   # ibSerialNumber
        model_oids=("1.3.6.1.4.1.7779.3.1.1.2.1.4.0",),    # ibHardwareType
        entity_mib_sparse=True,
    ),
    2636: VendorProfile(
        name="juniper",
        manufacturer="Juniper Networks",
        platform="Junos",
        # jnxBoxSerialNo first; SRX (clusters and some branch boxes) leave it
        # empty and answer on the jnxContentsTable chassis row instead. That
        # table is indexed {container, L1, L2, L3} (JUNIPER-MIB, jnxContents-
        # Entry INDEX clause): the chassis is container 1 and its row is
        # 1.1.0.0 — or 1.0.0.0 where L1 is "zero if unavailable", the MIB's
        # own wording. Both instances are tried; exact GETs, no walk needed.
        serial_oids=(
            "1.3.6.1.4.1.2636.3.1.3.0",              # jnxBoxSerialNo
            "1.3.6.1.4.1.2636.3.1.8.1.7.1.1.0.0",    # jnxContentsSerialNo, chassis row
            "1.3.6.1.4.1.2636.3.1.8.1.7.1.0.0.0",
        ),
        # jnxContentsModel is a dedicated model column ("SRX1500"), unlike
        # jnxBoxDescr, which is a sentence ("node0 Juniper SRX1500 Internet
        # Router" on clusters) — prefer the field, keep the sentence as the
        # last resort and let the model tidier strip the node/vendor noise.
        model_oids=(
            "1.3.6.1.4.1.2636.3.1.8.1.14.1.1.0.0",   # jnxContentsModel, chassis row
            "1.3.6.1.4.1.2636.3.1.8.1.14.1.0.0.0",
            "1.3.6.1.4.1.2636.3.1.2.0",              # jnxBoxDescr
        ),
        # "Juniper Networks, Inc. ex4300-48t ... JUNOS 21.4R3-S4.9 ..."
        version_patterns=(r"JUNOS\s+([\w.\-]+)", r"[Kk]ernel JUNOS\s+([\w.\-]+)"),
    ),
    # Blue Coat / Symantec ProxySG. ENTITY-MIB is not implemented on SGOS, so
    # everything comes from BLUECOAT-SG-PROXY-MIB scalars plus sysDescr. The
    # sysDescr wording is wire-verified from a captured walk (librenms test
    # corpus, tests/snmpsim/sgos.snmprec):
    #   "Blue Coat SG-S400 Series, Version: SGOS 6.6.5.2, Release id: 193348
    #    Proxy Edition"
    # The MIB has no model object at all — the products subtree under
    # 3417.1.1 encodes the model in sysObjectID, which is exactly the lookup-
    # table game this scanner refuses to play — so the model comes from the
    # device's own sysDescr words.
    3417: VendorProfile(
        name="bluecoat",
        manufacturer="Blue Coat",
        platform="SGOS",
        version_oids=("1.3.6.1.4.1.3417.2.11.1.3.0",),     # sgProxyVersion
        serial_oids=("1.3.6.1.4.1.3417.2.11.1.4.0",),      # sgProxySerialNumber
        version_patterns=(r"SGOS\s+([0-9][\w.]*)",),
        model_patterns=(
            r"Blue\s?Coat\s+(SG[\w-]+)",
            r"Symantec\s+(SG[\w-]+)",
        ),
        entity_mib_sparse=True,
    ),
    # Opengear publishes no version scalar we can rely on across their console
    # server range, so this profile is sysDescr-only by design.
    25049: VendorProfile(
        name="opengear",
        manufacturer="Opengear",
        platform="Opengear",
        version_patterns=(r"[Vv]ersion\s+([0-9][\w.\-]*)", r"\b([0-9]+\.[0-9]+\.[0-9]+)\b"),
        # "Opengear CM7148-2-DAC console server version 4.13.0"
        model_patterns=(r"Opengear\s+([A-Z]{2}\d[\w\-]*)",),
        entity_mib_sparse=True,
    ),
}


def profile_for_sysobjectid(sys_object_id: str) -> VendorProfile | None:
    """Pick a vendor profile from the enterprise arc of sysObjectID.

    Returns None for an unknown vendor — the scanner still records everything
    the standard MIBs give it, which for most gear is the model, serial,
    modules and interfaces. An unknown vendor loses only the vendor-specific
    extras.
    """
    enterprise = enterprise_number(sys_object_id)
    if enterprise is None:
        return None
    return PROFILES.get(enterprise)


def enterprise_number(sys_object_id: str) -> int | None:
    """Extract N from an OID of the form 1.3.6.1.4.1.N.<anything>."""
    if not sys_object_id:
        return None
    oid = sys_object_id.strip().lstrip(".")
    prefix = "1.3.6.1.4.1."
    if not oid.startswith(prefix):
        return None
    rest = oid[len(prefix):].split(".", 1)[0]
    try:
        return int(rest)
    except ValueError:
        return None


def extract_model(sys_descr: str, patterns: tuple[str, ...]) -> str:
    """Pull a model out of sysDescr for vendors that publish it nowhere else.

    Unlike version extraction there is no generic fallback: a wrong model is
    exactly the failure this scanner exists to prevent, so a vendor either has
    an explicit pattern or reports no model at all.
    """
    if not sys_descr or not patterns:
        return ""
    for pattern in patterns:
        match = re.search(pattern, sys_descr)
        if match:
            return match.group(1).strip().rstrip(",")
    return ""


def extract_version(sys_descr: str, patterns: tuple[str, ...]) -> str:
    """Pull a software version out of sysDescr using the profile's patterns.

    Falls back to the generic patterns so an unrecognised vendor still usually
    gets a version. Returns "" when nothing matches, which the sync layer
    treats as "leave whatever NetBox already has alone" rather than blanking a
    version somebody entered by hand.
    """
    if not sys_descr:
        return ""
    for pattern in tuple(patterns) + GENERIC_VERSION_PATTERNS:
        match = re.search(pattern, sys_descr)
        if match:
            return match.group(1).strip().rstrip(",")
    return ""


# sysDescr hints that identify the OS family more precisely than the vendor
# profile's default platform. Cisco is the case that matters: one enterprise
# number covers IOS, IOS-XE and NX-OS, and they are different platforms for
# anyone filtering NetBox by platform.
PLATFORM_HINTS: tuple[tuple[str, str, str], ...] = (
    ("cisco", "NX-OS", "Cisco NX-OS"),
    ("cisco", "IOS-XE", "Cisco IOS-XE"),
    ("cisco", "IOS XE", "Cisco IOS-XE"),
    # IOS-XE boxes usually only give themselves away through the image name,
    # e.g. "Catalyst L3 Switch Software (CAT9K_IOSXE)" — with no separator.
    ("cisco", "IOSXE", "Cisco IOS-XE"),
    ("cisco", "Adaptive Security Appliance", "Cisco ASA"),
    ("aruba", "ArubaOS-CX", "ArubaOS-CX"),
    ("aruba", "ClearPass", "Aruba ClearPass"),
)


def platform_for(profile: VendorProfile | None, sys_descr: str) -> str:
    """Resolve the NetBox Platform name (OS family) for a device."""
    if profile is None:
        return ""
    for vendor_name, needle, platform in PLATFORM_HINTS:
        if profile.name == vendor_name and needle.lower() in (sys_descr or "").lower():
            return platform
    return profile.platform
