# Where every OID came from

Each numeric OID in `snmpinv/mibs.py` and `snmpinv/vendors.py` was resolved
from the vendor's published MIB by parsing the file, not recalled from memory.
This document records the source so the next person can re-derive them.

That sounds like overkill for a handful of numbers. It is not: a mis-transcribed
column reads a real value out of the wrong field and produces a confident wrong
answer, which is the same failure mode as the sysObjectID model guessing this
scanner exists to replace. Two errors were caught this way during development,
both of which would have shipped:

- **`cswSwitchRole` is column 3, not 2.** `cswSwitchNumNextReload` occupies
  column 2. Reading column 2 as the role would have made every stack member
  look like it had role "= its own switch number", so member 1 would have been
  elected master by accident and member 4 would have had an invalid role.
- **`svnVersion` briefly resolved into Aruba's enterprise tree** (14823)
  instead of Check Point's (2620), because the first resolver pass treated MIB
  object names as a single global namespace. MIB names are scoped per module.
  Resolution is done per-vendor for this reason.

## Method

MIBs were fetched from the [librenms](https://github.com/librenms/librenms/tree/master/mibs)
and [netdisco-mibs](https://github.com/netdisco/netdisco-mibs) collections, then
parsed with a small resolver that reads every `name ::= { parent N }`
assignment and resolves each name to a numeric OID by walking to a known root.

`snmptranslate` was tried first and could not be used: Debian and Ubuntu ship
net-snmp with the IETF base MIBs stripped out for licensing reasons, so the
import chains do not resolve on a stock box. That same gap is why the scanner
uses numeric OIDs everywhere and sets `MIBS=""` — see `snmpinv/snmp.py`.

The resolver is committed alongside this file so the table below can be
re-checked rather than trusted:

```bash
mkdir -p ~/mibs/paloalto && cd ~/mibs/paloalto
curl -fsSLO https://raw.githubusercontent.com/netdisco/netdisco-mibs/master/paloalto/PAN-COMMON-MIB.my
curl -fsSLO https://raw.githubusercontent.com/netdisco/netdisco-mibs/master/paloalto/PAN-GLOBAL-REG-MIB.my
python3 docs/resolve_oid.py ~/mibs/paloalto panSysSwVersion panSysSerialNumber
```

Give each vendor its own directory — see the note in `resolve_oid.py` about
why parsing several at once produces wrong answers.

## Standard MIBs

| Object | OID | MIB |
|---|---|---|
| `sysDescr` | 1.3.6.1.2.1.1.1.0 | SNMPv2-MIB |
| `sysObjectID` | 1.3.6.1.2.1.1.2.0 | SNMPv2-MIB |
| `sysName` | 1.3.6.1.2.1.1.5.0 | SNMPv2-MIB |
| `entPhysicalTable` | 1.3.6.1.2.1.47.1.1.1.1 | ENTITY-MIB |
| `entPhysicalClass` | …1.5 | ENTITY-MIB |
| `entPhysicalContainedIn` | …1.4 | ENTITY-MIB |
| `entPhysicalSerialNum` | …1.11 | ENTITY-MIB |
| `entPhysicalMfgName` | …1.12 | ENTITY-MIB |
| `entPhysicalModelName` | …1.13 | ENTITY-MIB |
| `ifTable` | 1.3.6.1.2.1.2.2.1 | IF-MIB |
| `ifXTable` | 1.3.6.1.2.1.31.1.1.1 | IF-MIB |
| `ipAddressTable` | 1.3.6.1.2.1.4.34.1 | IP-MIB |
| `ipAddrTable` (deprecated) | 1.3.6.1.2.1.4.20.1 | RFC1213-MIB |

`entPhysicalClass` values used: `chassis(3)`, `container(5)`, `powerSupply(6)`,
`module(9)`, `stack(11)`.

### ipAddressTable prefix length

The prefix length is **not a column**. `ipAddressPrefix` (…1.5) is a RowPointer
into `ipAddressPrefixTable`, and the length is the *last sub-identifier* of the
OID it points at. `collect.py::_prefix_length_from_pointer` decodes it, and the
fixtures encode it properly so the decoding is actually exercised.

## CISCO-STACKWISE-MIB

Source: `librenms/mibs/cisco/CISCO-STACKWISE-MIB`. Root is
`{ ciscoMgmt 500 }` = 1.3.6.1.4.1.9.9.500.

`cswSwitchInfoTable` = 1.3.6.1.4.1.9.9.500.1.2.1, entry `.1`.

**`INDEX { entPhysicalIndex }`** — this is the important structural fact. Each
row's index is an ENTITY-MIB physical index, which is what lets a member's
switch number and role be joined directly to the chassis entity carrying that
member's own serial and model.

| Column | Object |
|---|---|
| 1 | `cswSwitchNumCurrent` |
| 2 | `cswSwitchNumNextReload` |
| **3** | **`cswSwitchRole`** |
| 4 | `cswSwitchSwPriority` |
| 5 | `cswSwitchHwPriority` |
| **6** | **`cswSwitchState`** |
| 7 | `cswSwitchMacAddress` |
| 8 | `cswSwitchSoftwareImage` |

`cswSwitchRole`: `master(1)`, `member(2)`, `notMember(3)`, `standby(4)`.

`cswSwitchState`: `waiting(1)`, `progressing(2)`, `added(3)`, `ready(4)`,
`sdmMismatch(5)`, `verMismatch(6)`, `featureMismatch(7)`, `newMasterInit(8)`,
`provisioned(9)`, `invalid(10)`, `removed(11)`.

Only states 1–8 count as physically present. `provisioned(9)` means the member
is configured but the hardware is absent, and creating a NetBox device for it
would be inventing a switch that is not in the rack.

## Vendor scalars

| Vendor | Object | OID | MIB |
|---|---|---|---|
| Palo Alto | `panSysSwVersion` | 1.3.6.1.4.1.25461.2.1.2.1.1.0 | PAN-COMMON-MIB |
| Palo Alto | `panSysHwVersion` | 1.3.6.1.4.1.25461.2.1.2.1.2.0 | PAN-COMMON-MIB |
| Palo Alto | `panSysSerialNumber` | 1.3.6.1.4.1.25461.2.1.2.1.3.0 | PAN-COMMON-MIB |
| Fortinet | `fgSysVersion` | 1.3.6.1.4.1.12356.101.4.1.1.0 | FORTINET-FORTIGATE-MIB |
| Fortinet | `fnSysSerial` | 1.3.6.1.4.1.12356.100.1.1.1.0 | FORTINET-CORE-MIB |
| F5 | `sysProductVersion` | 1.3.6.1.4.1.3375.2.1.4.2.0 | F5-BIGIP-SYSTEM-MIB |
| F5 | `sysGeneralChassisSerialNum` | 1.3.6.1.4.1.3375.2.1.3.3.3.0 | F5-BIGIP-SYSTEM-MIB |
| F5 | `sysPlatformInfoMarketingName` | 1.3.6.1.4.1.3375.2.1.3.5.2.0 | F5-BIGIP-SYSTEM-MIB |
| Check Point | `svnVersion` | 1.3.6.1.4.1.2620.1.6.4.1.0 | CHECKPOINT-MIB |
| Check Point | `svnApplianceSerialNumber` | 1.3.6.1.4.1.2620.1.6.16.3.0 | CHECKPOINT-MIB |
| Check Point | `svnApplianceProductName` | 1.3.6.1.4.1.2620.1.6.16.7.0 | CHECKPOINT-MIB |
| Infoblox | `ibHardwareType` | 1.3.6.1.4.1.7779.3.1.1.2.1.4.0 | IB-PLATFORMONE-MIB |
| Infoblox | `ibSerialNumber` | 1.3.6.1.4.1.7779.3.1.1.2.1.6.0 | IB-PLATFORMONE-MIB |
| Infoblox | `ibNiosVersion` | 1.3.6.1.4.1.7779.3.1.1.2.1.7.0 | IB-PLATFORMONE-MIB |
| Juniper | `jnxBoxDescr` | 1.3.6.1.4.1.2636.3.1.2.0 | JUNIPER-MIB |
| Juniper | `jnxBoxSerialNo` | 1.3.6.1.4.1.2636.3.1.3.0 | JUNIPER-MIB |
| Aruba | `wlsxSysExtHostname` | 1.3.6.1.4.1.14823.2.2.1.2.1.2.0 | WLSX-SYSTEMEXT-MIB |
| Aruba | `wlsxSysExtModelName` | 1.3.6.1.4.1.14823.2.2.1.2.1.3.0 | WLSX-SYSTEMEXT-MIB |
| Aruba | `wlsxSysExtLicenseSerialNumber` | 1.3.6.1.4.1.14823.2.2.1.2.1.11.0 | WLSX-SYSTEMEXT-MIB |

Infoblox chain, since it is several hops:
`infoblox { enterprises 7779 }` → `ibSNMP { infoblox 3 }` →
`ibProduct { ibSNMP 1 }` → `ibOne { ibProduct 1 }` →
`ibPlatformOne { ibOne 2 }` → `ibPlatformModule { ibPlatformOne 1 }`.

Aruba chain: `aruba { enterprises 14823 }` →
`arubaEnterpriseMibModules { aruba 2 }` → `switch { … 2 }` →
`wlsxEnterpriseMibModules { switch 1 }`.

## Aruba access points

`wlsxWlanAPTable` = 1.3.6.1.4.1.14823.2.2.1.5.2.1.4, entry `.1`
(WLSX-WLAN-MIB).

**`INDEX { wlanAPMacAddress }`** — the AP's MAC is the row index, so it appears
in the OID suffix as six decimal sub-identifiers and never as a column of its
own. Column 1 is not-accessible and never appears in a walk;
`collect.py::_decode_mac_index` reconstructs the MAC from the index.

| Column | Object |
|---|---|
| 2 | `wlanAPIpAddress` |
| 3 | `wlanAPName` |
| 4 | `wlanAPGroupName` |
| 6 | `wlanAPSerialNumber` |
| 13 | `wlanAPModelName` |
| 19 | `wlanAPStatus` |

## Manufacturer identification

`sysObjectID` is used **only** for the enterprise arc — the `N` in
`1.3.6.1.4.1.N` — which reliably names the vendor. Nothing below that arc is
consulted. The sub-arcs are what encode a model, and decoding them through a
lookup table is exactly the behaviour that produced device types like
`aristaDCS7050SX272Q`.

`entPhysicalMfgName` is preferred over the enterprise map whenever the device
supplies it.
