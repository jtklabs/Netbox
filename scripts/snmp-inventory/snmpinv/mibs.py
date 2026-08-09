"""Numeric OIDs and value maps for everything the scanner collects.

Numeric OIDs on purpose. A poller box should not need a MIB tree installed —
net-snmp only resolves names like `entPhysicalModelName` if the matching MIB
file is present, and on a stock Ubuntu box it is not (Debian/Ubuntu ship
net-snmp with the IETF MIBs stripped out for licensing reasons). Numeric OIDs
work everywhere and never depend on `snmp-mibs-downloader` or MIBDIRS.

Column numbers below were taken from the published MIBs, not from memory:
CISCO-STACKWISE-MIB in particular is easy to get wrong — cswSwitchRole is
column 3, not 2, because cswSwitchNumNextReload sits at column 2.
"""

from __future__ import annotations

# --- SNMPv2-MIB: the system group -------------------------------------------

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

SYSTEM_GROUP = "1.3.6.1.2.1.1"

# --- ENTITY-MIB: entPhysicalTable -------------------------------------------
#
# This is the whole point of the scanner. The device reports its own model and
# serial here; we never guess either from sysObjectID.

ENT_PHYSICAL_TABLE = "1.3.6.1.2.1.47.1.1.1.1"

ENT_DESCR = f"{ENT_PHYSICAL_TABLE}.2"
ENT_VENDOR_TYPE = f"{ENT_PHYSICAL_TABLE}.3"
ENT_CONTAINED_IN = f"{ENT_PHYSICAL_TABLE}.4"
ENT_CLASS = f"{ENT_PHYSICAL_TABLE}.5"
ENT_PARENT_REL_POS = f"{ENT_PHYSICAL_TABLE}.6"
ENT_NAME = f"{ENT_PHYSICAL_TABLE}.7"
ENT_HARDWARE_REV = f"{ENT_PHYSICAL_TABLE}.8"
ENT_FIRMWARE_REV = f"{ENT_PHYSICAL_TABLE}.9"
ENT_SOFTWARE_REV = f"{ENT_PHYSICAL_TABLE}.10"
ENT_SERIAL_NUM = f"{ENT_PHYSICAL_TABLE}.11"
ENT_MFG_NAME = f"{ENT_PHYSICAL_TABLE}.12"
ENT_MODEL_NAME = f"{ENT_PHYSICAL_TABLE}.13"
ENT_ALIAS = f"{ENT_PHYSICAL_TABLE}.14"
ENT_ASSET_ID = f"{ENT_PHYSICAL_TABLE}.15"
ENT_IS_FRU = f"{ENT_PHYSICAL_TABLE}.16"

# entPhysicalClass. A stack reports one `chassis` row per member switch, which
# is exactly how we detect stack membership when STACKWISE is unavailable.
ENT_CLASS_OTHER = 1
ENT_CLASS_UNKNOWN = 2
ENT_CLASS_CHASSIS = 3
ENT_CLASS_BACKPLANE = 4
ENT_CLASS_CONTAINER = 5
ENT_CLASS_POWER_SUPPLY = 6
ENT_CLASS_FAN = 7
ENT_CLASS_SENSOR = 8
ENT_CLASS_MODULE = 9
ENT_CLASS_PORT = 10
ENT_CLASS_STACK = 11
ENT_CLASS_CPU = 12

ENT_CLASS_NAMES = {
    ENT_CLASS_OTHER: "other",
    ENT_CLASS_UNKNOWN: "unknown",
    ENT_CLASS_CHASSIS: "chassis",
    ENT_CLASS_BACKPLANE: "backplane",
    ENT_CLASS_CONTAINER: "container",
    ENT_CLASS_POWER_SUPPLY: "powerSupply",
    ENT_CLASS_FAN: "fan",
    ENT_CLASS_SENSOR: "sensor",
    ENT_CLASS_MODULE: "module",
    ENT_CLASS_PORT: "port",
    ENT_CLASS_STACK: "stack",
    ENT_CLASS_CPU: "cpu",
}

# --- IF-MIB -----------------------------------------------------------------

IF_TABLE = "1.3.6.1.2.1.2.2.1"
IF_INDEX = f"{IF_TABLE}.1"
IF_DESCR = f"{IF_TABLE}.2"
IF_TYPE = f"{IF_TABLE}.3"
IF_MTU = f"{IF_TABLE}.4"
IF_SPEED = f"{IF_TABLE}.5"
IF_PHYS_ADDRESS = f"{IF_TABLE}.6"
IF_ADMIN_STATUS = f"{IF_TABLE}.7"
IF_OPER_STATUS = f"{IF_TABLE}.8"

IF_X_TABLE = "1.3.6.1.2.1.31.1.1.1"
IF_NAME = f"{IF_X_TABLE}.1"
IF_HIGH_SPEED = f"{IF_X_TABLE}.15"
IF_ALIAS = f"{IF_X_TABLE}.18"

IF_ADMIN_UP = 1
IF_OPER_UP = 1

# --- IP-MIB -----------------------------------------------------------------
#
# ipAddressTable is the current table and carries IPv6; ipAddrTable is the
# deprecated IPv4-only one that older gear still answers. We try the new one
# and fall back, because plenty of fielded switches answer only the old one.

IP_ADDRESS_TABLE = "1.3.6.1.2.1.4.34.1"
IP_ADDRESS_IF_INDEX = f"{IP_ADDRESS_TABLE}.3"
IP_ADDRESS_TYPE = f"{IP_ADDRESS_TABLE}.4"
IP_ADDRESS_PREFIX = f"{IP_ADDRESS_TABLE}.5"
IP_ADDRESS_ORIGIN = f"{IP_ADDRESS_TABLE}.6"
IP_ADDRESS_STATUS = f"{IP_ADDRESS_TABLE}.7"

# Legacy IPv4-only table, indexed by the address itself.
IP_ADDR_TABLE = "1.3.6.1.2.1.4.20.1"
IP_AD_ENT_ADDR = f"{IP_ADDR_TABLE}.1"
IP_AD_ENT_IF_INDEX = f"{IP_ADDR_TABLE}.2"
IP_AD_ENT_NETMASK = f"{IP_ADDR_TABLE}.3"

# InetAddressType, the first index element of ipAddressTable.
INET_TYPE_IPV4 = 1
INET_TYPE_IPV6 = 2

# --- CISCO-STACKWISE-MIB ----------------------------------------------------
#
# cswSwitchInfoTable is INDEXED BY entPhysicalIndex, which is what lets us join
# a stack member's switch number and role straight onto its ENTITY-MIB chassis
# row (and therefore onto its own serial and model).

CSW_SWITCH_INFO_TABLE = "1.3.6.1.4.1.9.9.500.1.2.1.1"
CSW_SWITCH_NUM_CURRENT = f"{CSW_SWITCH_INFO_TABLE}.1"
CSW_SWITCH_NUM_NEXT_RELOAD = f"{CSW_SWITCH_INFO_TABLE}.2"
CSW_SWITCH_ROLE = f"{CSW_SWITCH_INFO_TABLE}.3"
CSW_SWITCH_SW_PRIORITY = f"{CSW_SWITCH_INFO_TABLE}.4"
CSW_SWITCH_HW_PRIORITY = f"{CSW_SWITCH_INFO_TABLE}.5"
CSW_SWITCH_STATE = f"{CSW_SWITCH_INFO_TABLE}.6"
CSW_SWITCH_MAC_ADDRESS = f"{CSW_SWITCH_INFO_TABLE}.7"
CSW_SWITCH_SOFTWARE_IMAGE = f"{CSW_SWITCH_INFO_TABLE}.8"

CSW_ROLE_MASTER = 1
CSW_ROLE_MEMBER = 2
CSW_ROLE_NOT_MEMBER = 3
CSW_ROLE_STANDBY = 4

CSW_ROLE_NAMES = {
    CSW_ROLE_MASTER: "master",
    CSW_ROLE_MEMBER: "member",
    CSW_ROLE_NOT_MEMBER: "notMember",
    CSW_ROLE_STANDBY: "standby",
}

CSW_STATE_READY = 4
CSW_STATE_PROVISIONED = 9
CSW_STATE_REMOVED = 11

CSW_STATE_NAMES = {
    1: "waiting",
    2: "progressing",
    3: "added",
    CSW_STATE_READY: "ready",
    5: "sdmMismatch",
    6: "verMismatch",
    7: "featureMismatch",
    8: "newMasterInit",
    CSW_STATE_PROVISIONED: "provisioned",
    10: "invalid",
    CSW_STATE_REMOVED: "removed",
}

# A member that is only provisioned (configured but physically absent) or has
# been removed should not become a Device in NetBox — there is no hardware.
CSW_STATES_PRESENT = {1, 2, 3, CSW_STATE_READY, 5, 6, 7, 8}

# --- Vendor identification --------------------------------------------------
#
# sysObjectID is *only* used to name the manufacturer, never the model: the
# enterprise arc (1.3.6.1.4.1.<N>) reliably identifies the vendor, while the
# sub-arcs that encode a model are the guesswork that produced device types
# like "aristaDCS7050SX272Q". entPhysicalMfgName is preferred over this map
# whenever the device supplies it.

ENTERPRISE_PREFIX = "1.3.6.1.4.1."

ENTERPRISE_MANUFACTURERS = {
    9: "Cisco",
    11: "HPE",
    43: "3Com",
    171: "D-Link",
    193: "Ericsson",
    207: "Allied Telesis",
    674: "Dell",
    789: "NetApp",
    1588: "Broadcom",
    1916: "Extreme Networks",
    1991: "Foundry Networks",
    2011: "Huawei",
    2352: "Zhone",
    2620: "Check Point",
    2636: "Juniper Networks",
    3224: "Netscreen",
    3375: "F5 Networks",
    4526: "Netgear",
    4874: "Adtran",
    5951: "NetScaler",
    6027: "Force10",
    6486: "Alcatel-Lucent",
    6527: "Nokia",
    8072: "net-snmp",
    9694: "Aruba Networks",
    10002: "Ubiquiti",
    11863: "TP-Link",
    12356: "Fortinet",
    12532: "SonicWall",
    14179: "Aruba Networks",
    14525: "Aruba Networks",
    14823: "Aruba Networks",
    14988: "MikroTik",
    16057: "Aruba Networks",
    18011: "Aruba Networks",
    21091: "Aruba Networks",
    25053: "Ruckus",
    25461: "Palo Alto Networks",
    26543: "IBM",
    29671: "Cisco Meraki",
    30065: "Arista Networks",
    35265: "Cambium Networks",
    41112: "Ubiquiti",
    47196: "Aruba Networks",
    52642: "Aruba Networks",
}

# --- ifType -> NetBox interface type ----------------------------------------
#
# IANAifType values we care about. Anything not listed becomes "other" rather
# than being dropped, so an unexpected ifType never loses an interface.

IF_TYPE_OTHER = 1
IF_TYPE_ETHERNET = 6
IF_TYPE_ISO88023 = 7
IF_TYPE_PPP = 23
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_SLIP = 28
IF_TYPE_FRAME_RELAY = 32
IF_TYPE_RS232 = 33
IF_TYPE_ATM = 37
IF_TYPE_SONET = 39
IF_TYPE_MODEM = 48
IF_TYPE_PROP_VIRTUAL = 53
IF_TYPE_FAST_ETHER = 62
IF_TYPE_FAST_ETHER_FX = 69
IF_TYPE_IEEE80211 = 71
IF_TYPE_GIGABIT_ETHERNET = 117
IF_TYPE_TUNNEL = 131
IF_TYPE_L2_VLAN = 135
IF_TYPE_L3_IPVLAN = 136
IF_TYPE_IEEE8023AD_LAG = 161
IF_TYPE_BRIDGE = 209
IF_TYPE_STACK_SUBIF = 202

# Types that carry no hardware of their own. NetBox calls all of these
# "virtual" apart from bridges, which have their own type.
IF_TYPES_VIRTUAL = {
    IF_TYPE_SOFTWARE_LOOPBACK,
    IF_TYPE_PROP_VIRTUAL,
    IF_TYPE_TUNNEL,
    IF_TYPE_L2_VLAN,
    IF_TYPE_L3_IPVLAN,
    IF_TYPE_STACK_SUBIF,
}

# ifTypes that are Ethernet in some form and should be resolved by speed.
IF_TYPES_ETHERNET = {
    IF_TYPE_ETHERNET,
    IF_TYPE_ISO88023,
    IF_TYPE_FAST_ETHER,
    IF_TYPE_FAST_ETHER_FX,
    IF_TYPE_GIGABIT_ETHERNET,
}

NETBOX_TYPE_VIRTUAL = "virtual"
NETBOX_TYPE_BRIDGE = "bridge"
NETBOX_TYPE_LAG = "lag"
NETBOX_TYPE_OTHER = "other"

# ifHighSpeed (Mbps) -> NetBox interface type slug.
#
# Every slug here was checked against a live NetBox 4.6.7 (216 choices) — the
# names have churned across NetBox releases, so do not add entries from memory.
# Two defaults per speed: gigabit and below are almost always fixed copper,
# 10G and above are almost always pluggable optics. `refine_by_name` below
# corrects the common exceptions using the interface name.
SPEED_TO_NETBOX_TYPE = {
    100: "100base-tx",
    1000: "1000base-t",
    2500: "2.5gbase-t",
    5000: "5gbase-t",
    10000: "10gbase-x-sfpp",
    25000: "25gbase-x-sfp28",
    40000: "40gbase-x-qsfpp",
    50000: "50gbase-x-sfp56",
    100000: "100gbase-x-qsfp28",
    200000: "200gbase-x-qsfp56",
    400000: "400gbase-x-qsfpdd",
}

# NetBox has no 10 Mbps Ethernet type at all, so a 10 Mbps port has to land on
# "other" rather than being silently promoted to 100base-tx.
SPEED_NO_NETBOX_TYPE = {10}

# Interface-name hints that override the speed default. Cisco names the media
# in the interface name, which is more reliable than guessing from speed alone:
# a 1G port called "TenGigabitEthernet1/1/1" running at 1000 Mbps is still an
# SFP+ cage, and a 10G port called "TenGigabitEthernet" on a 9300 is copper
# only when the model says so — so we only correct the cases that are safe.
_COPPER_10G_HINTS = ("gigabitethernet0/0", "management", "mgmt")


def netbox_interface_type(if_type: int, high_speed_mbps: int | None, name: str = "") -> str:
    """Map an IF-MIB ifType (+ ifHighSpeed) onto a NetBox interface type slug.

    Unknown ifTypes and unknown speeds both fall back to "other" rather than
    raising — an interface with an odd type is still an interface worth having
    in NetBox, and a wrong-but-present type is easy to fix by hand later.
    """
    if if_type == IF_TYPE_IEEE8023AD_LAG:
        return NETBOX_TYPE_LAG
    if if_type == IF_TYPE_BRIDGE:
        return NETBOX_TYPE_BRIDGE
    if if_type in IF_TYPES_VIRTUAL:
        return NETBOX_TYPE_VIRTUAL
    if if_type == IF_TYPE_IEEE80211:
        # Without a radio MIB we cannot tell 11n from 11ax, and guessing the
        # wrong generation is worse than saying "wireless of some kind".
        return "other-wireless"
    if if_type in IF_TYPES_ETHERNET:
        if not high_speed_mbps or high_speed_mbps in SPEED_NO_NETBOX_TYPE:
            return NETBOX_TYPE_OTHER
        slug = SPEED_TO_NETBOX_TYPE.get(high_speed_mbps)
        if slug is None:
            return NETBOX_TYPE_OTHER
        return refine_by_name(slug, name)
    return NETBOX_TYPE_OTHER


def refine_by_name(slug: str, name: str) -> str:
    """Correct the copper/optical default for the cases the name settles.

    Only the unambiguous ones: a 10G interface whose name says it is the
    out-of-band management port is RJ45, not SFP+.
    """
    if not name:
        return slug
    lowered = name.lower()
    if slug == "10gbase-x-sfpp" and any(h in lowered for h in _COPPER_10G_HINTS):
        return "10gbase-t"
    return slug
