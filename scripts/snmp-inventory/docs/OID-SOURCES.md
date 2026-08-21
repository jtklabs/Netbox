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
| Check Point | `svnServicePack` | 1.3.6.1.4.1.2620.1.6.999.0 | CHECKPOINT-MIB |
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
| F5 | `sysProductBuild` | 1.3.6.1.4.1.3375.2.1.4.3.0 | F5-BIGIP-SYSTEM-MIB |
| Juniper | `jnxContentsDescr` | 1.3.6.1.4.1.2636.3.1.8.1.6 | JUNIPER-MIB (mib-jnx-chassis) |
| Juniper | `jnxContentsSerialNo` | 1.3.6.1.4.1.2636.3.1.8.1.7 | JUNIPER-MIB (mib-jnx-chassis) |
| Juniper | `jnxContentsModel` | 1.3.6.1.4.1.2636.3.1.8.1.14 | JUNIPER-MIB (mib-jnx-chassis) |
| Blue Coat | `sgProxySoftware` | 1.3.6.1.4.1.3417.2.11.1.2.0 | BLUECOAT-SG-PROXY-MIB |
| Blue Coat | `sgProxyVersion` | 1.3.6.1.4.1.3417.2.11.1.3.0 | BLUECOAT-SG-PROXY-MIB |
| Blue Coat | `sgProxySerialNumber` | 1.3.6.1.4.1.3417.2.11.1.4.0 | BLUECOAT-SG-PROXY-MIB |

`svnServicePack` (`{ svn 999 }`, Gauge32, resolved 2026-08-20) is described in
the MIB only as "SVN service pack", but on Gaia it returns the installed
**Jumbo Hotfix take** — the number a Check Point admin means by "patch level".
It is joined to `svnVersion` as `R81.20 Take 89` via the profile's
`build_format`. A GA install with no jumbo reports take 0, which the collector
treats as "no build" — otherwise every unpatched gateway would read
"Take 0" as if that were a patch level.

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
| 34 | `wlanAPSwVersion` |

`wlanAPStatus` is an `ArubaAPStatus` — `up(1)`, `down(2)` — confirmed from
the ARUBA-TC textual-conventions module (netdisco-mibs `aruba/aruba-tc.my`),
which is where the constant `ARUBA_AP_STATUS_UP = 1` comes from.

`wlanAPSwVersion` (column 34, resolved 2026-08-20 from the cached
WLSX-WLAN-MIB) is the per-AP running image and the preferred version source;
APs whose row omits it inherit the controller's version, since campus APs run
the image the controller pushes. Some ArubaOS builds leave the column empty —
both paths are exercised in the fixtures.

## Manufacturer identification

`sysObjectID` is used **only** for the enterprise arc — the `N` in
`1.3.6.1.4.1.N` — which reliably names the vendor. Nothing below that arc is
consulted. The sub-arcs are what encode a model, and decoding them through a
lookup table is exactly the behaviour that produced device types like
`aristaDCS7050SX272Q`.

`entPhysicalMfgName` is preferred over the enterprise map whenever the device
supplies it.

**Provenance of the map itself**: every number in `ENTERPRISE_MANUFACTURERS`
was checked against the IANA Private Enterprise Numbers registry
(<https://www.iana.org/assignments/enterprise-numbers.txt>) on 2026-08-20.
That audit found the table's original content had EIGHT arcs labelled "Aruba
Networks" of which only 14823 was Aruba's — the others were Arbor Networks
(9694), Airespace/**Cisco** (14179, AireOS WLCs), Trapeze/**Juniper**
(14525), EventGnosis (16057, removed), Apollo Communications (18011,
removed), Exinda (21091), HPE (47196) and FS.COM (52642) — plus 4874
labelled Adtran (it is Juniper/Unisphere; Adtran is 664), 12532 labelled
SonicWall (it is Neoteris/Pulse Secure; SonicWall is 8741), 2352 labelled
Zhone (it is Redback/Ericsson; Zhone is 5504) and 35265 labelled Cambium (it
is Eltex; Cambium is 17713). One deliberate divergence from the registry
remains: 10002 is registered to Frogfoot Networks but kept as Ubiquiti,
because airOS radios answer with sysObjectID under it in the field.
`tests/test_enterprise_map.py` pins the corrections.

## Juniper: product name vs FRU model name

Two objects, two NetBox fields, checked against the JUNIPER-MIB text
(mib-jnx-chassis) on 2026-08-21:

| Object | MIB DESCRIPTION (verbatim) | Written to |
|---|---|---|
| `jnxBoxDescr` (…3.1.2.0) | "The name, model, or detailed description of the box, indicating which product the box is about, for example 'M40'." | `DeviceType.model`, after the model tidier strips a `node0 ` prefix, the `Juniper` prefix and the role suffix ("Internet Router", "Ethernet Switch", "Services Gateway", "Internet Backbone Router") — "node0 Juniper SRX1500 Internet Router" → `SRX1500`. The same reduction the fleet's old `Juniper\s+(.*?)\s+Internet` regex did, covering the non-"Internet" suffixes too. |
| `jnxContentsModel` (…8.1.14, chassis row) | "The FRU model name of this subject, blank if unknown or unavailable." | `DeviceType.part_number` — the orderable identifier ("SRX1500-SYS-JB"-shaped). It first shipped as the model name and read as a raw number; it is the right thing for the part number and the wrong thing for the name. |
| `jnxContentsPartNo` (…8.1.10) | "The part number of this subject, blank if unknown or unavailable." | not used — Juniper's internal numeric part number, rawer still. |

## Juniper jnxContents chassis-row instances

`jnxContentsTable` is indexed `{ jnxContentsContainerIndex, L1, L2, L3 }`
(JUNIPER-MIB, `jnxContentsEntry` INDEX clause). The chassis is container 1;
its row is `1.1.0.0`, or `1.0.0.0` on platforms where L1 is "zero if
unavailable or inapplicable" — the MIB's own wording for the L-indexes. The
profile GETs both instances of `jnxContentsSerialNo` and `jnxContentsModel`
as fallbacks for SRX, which (clustered and some branch boxes) answer an empty
`jnxBoxSerialNo`. Fetch used:

```bash
mkdir -p ~/mibs/juniper && cd ~/mibs/juniper
curl -fsSLO https://raw.githubusercontent.com/netdisco/netdisco-mibs/master/juniper/mib-jnx-smi.txt
curl -fsSLO https://raw.githubusercontent.com/netdisco/netdisco-mibs/master/juniper/mib-jnx-chassis.txt
./resolve_oid.py ~/mibs/juniper jnxContentsSerialNo jnxContentsModel
```

## Blue Coat chain

`blueCoat { enterprises 3417 }` → `blueCoatMgmt { blueCoat 2 }` →
`bluecoatSGProxyMIB { blueCoatMgmt 11 }` → `sgProxyConfig { … 1 }`, objects
2/3/4. The MIB defines **no model object** — the products subtree under
3417.1.1 encodes the model in sysObjectID, which is the lookup-table game this
scanner refuses — so the model comes from sysDescr, whose wording is
wire-verified from a real captured walk (librenms test corpus,
`tests/snmpsim/sgos.snmprec`):

```
sysDescr    = Blue Coat SG-S400 Series, Version: SGOS 6.6.5.2, Release id: 193348 Proxy Edition
sysObjectID = 1.3.6.1.4.1.3417.1.1.37
```

```bash
mkdir -p ~/mibs/bluecoat && cd ~/mibs/bluecoat
curl -fsSLO https://raw.githubusercontent.com/librenms/librenms/master/mibs/bluecoat/BLUECOAT-MIB
curl -fsSLO https://raw.githubusercontent.com/librenms/librenms/master/mibs/bluecoat/BLUECOAT-SG-PROXY-MIB
./resolve_oid.py ~/mibs/bluecoat sgProxyVersion sgProxySerialNumber
```

## LLDP-MIB (IEEE 802.1AB)

**This is an IEEE MIB, not an IETF one: its root is `1.0.8802.1.1.2`, outside
the familiar 1.3.6.1 tree entirely.** `lldpMIB ::= { iso std(0) iso8802(8802)
ieee802dot1(1) ieee802dot1mibs(1) 2 }` — the assignment inlines its named
numbers, so the resolver anchors it from `iso` alone. Two practical
consequences of the unusual root, handled where they bite: a walk of 1.3.6.1
never sees LLDP (probe `--save-walk` walks both trees), and the emulator's
`pass_persist` registration at .1.3.6.1 does not cover it (a second
registration serves .1.0.8802).

```bash
mkdir -p ~/mibs/ieee && cd ~/mibs/ieee
curl -fsSLO https://raw.githubusercontent.com/librenms/librenms/master/mibs/LLDP-MIB
python3 docs/resolve_oid.py ~/mibs/ieee lldpRemTable lldpLocPortTable
```

`lldpRemTable` = 1.0.8802.1.1.2.1.4.1, entry `.1`.

**`INDEX { lldpRemTimeMark, lldpRemLocalPortNum, lldpRemIndex }`** — columns
1–3 are the index and never appear in a walk. `lldpRemLocalPortNum` is
formally an index into `lldpLocPortTable`, **not** an ifIndex: many platforms
number the two identically, but the MIB does not promise it, so the local port
is resolved through `lldpLocPortTable` whenever the device serves that table
and falls back to ifIndex (with a debug note) only when it does not.

| Column | Object |
|---|---|
| 4 | `lldpRemChassisIdSubtype` |
| 5 | `lldpRemChassisId` |
| 6 | `lldpRemPortIdSubtype` |
| 7 | `lldpRemPortId` |
| 8 | `lldpRemPortDesc` |
| 9 | `lldpRemSysName` |
| 10 | `lldpRemSysDesc` |
| 11 | `lldpRemSysCapSupported` |
| 12 | `lldpRemSysCapEnabled` |

`lldpLocPortTable` = 1.0.8802.1.1.2.1.3.7, entry `.1`, `INDEX {
lldpLocPortNum }`: `lldpLocPortIdSubtype` (2), `lldpLocPortId` (3),
`lldpLocPortDesc` (4).

Enumerations, from the MIB's own TEXTUAL-CONVENTIONs:

- `LldpChassisIdSubtype`: chassisComponent(1), interfaceAlias(2),
  portComponent(3), macAddress(4), networkAddress(5), interfaceName(6),
  local(7).
- `LldpPortIdSubtype`: interfaceAlias(1), portComponent(2), macAddress(3),
  networkAddress(4), interfaceName(5), agentCircuitId(6), local(7).
- `LldpSystemCapabilitiesMap` BITS: other(0), repeater(1), bridge(2),
  wlanAccessPoint(3), router(4), telephone(5), docsisCableDevice(6),
  stationOnly(7). BITS are an octet string on the wire with bit 0 as the
  high-order bit of the first octet (RFC 2578 §7.1.4), so bridge+router is
  0x28 — which is a printable `(`, meaning net-snmp may render the value as
  STRING rather than Hex-STRING and the decoder must accept both.

## CISCO-CDP-MIB

Source: `librenms/mibs/cisco/CISCO-CDP-MIB` (with CISCO-SMI for the
`ciscoMgmt` anchor and CISCO-TC for the address TCs). Root is
`{ ciscoMgmt 23 }` = 1.3.6.1.4.1.9.9.23.

```bash
mkdir -p ~/mibs/cisco && cd ~/mibs/cisco
curl -fsSLO https://raw.githubusercontent.com/librenms/librenms/master/mibs/cisco/CISCO-CDP-MIB
curl -fsSLO https://raw.githubusercontent.com/librenms/librenms/master/mibs/cisco/CISCO-SMI
curl -fsSLO https://raw.githubusercontent.com/librenms/librenms/master/mibs/cisco/CISCO-TC
python3 docs/resolve_oid.py ~/mibs/cisco cdpCacheTable cdpCacheDeviceId
```

`cdpCacheTable` = 1.3.6.1.4.1.9.9.23.1.2.1, entry `.1`.

**`INDEX { cdpCacheIfIndex, cdpCacheDeviceIndex }`** — the first index element
is "normally, the ifIndex value of the local interface" (the MIB's own
wording), so a CDP row joins straight onto the interface table; the second
distinguishes multiple neighbors heard on one port.

| Column | Object |
|---|---|
| 3 | `cdpCacheAddressType` (`CiscoNetworkProtocol`; `ip(1)` per CISCO-TC) |
| 4 | `cdpCacheAddress` (octet string; 4 bytes for ip(1)) |
| 5 | `cdpCacheVersion` |
| 6 | `cdpCacheDeviceId` |
| 7 | `cdpCacheDevicePort` |
| 8 | `cdpCachePlatform` |
| 9 | `cdpCacheCapabilities` |

`cdpCacheCapabilities` is an OCTET STRING whose bit meanings the MIB
deliberately does **not** enumerate — its DESCRIPTION defers to "the latest
version of the CDP specification" (REFERENCE: Cisco Discovery Protocol
Specification, 10/19/94). The low seven bits are stable across Cisco's public
CDP TLV documentation — 0x01 router, 0x02 transparent bridge, 0x04
source-route bridge, 0x08 switch, 0x10 host, 0x20 IGMP, 0x40 repeater — and
only those are decoded; higher bits are carried raw and shown as hex rather
than guessed at. Because the capability word is spec-referenced rather than
MIB-defined, the CDP neighbor-class filter leans on `cdpCachePlatform` (the
device naming itself in its own words: "Cisco IP Phone 7962", "cisco
AIR-AP2802I-B-K9") with the capability bits as a supplement.

## 2026-08-20 CDP/LLDP resolution, and a third resolver bug

Resolving CISCO-CDP-MIB found the third parser bug of the svnVersion class,
and this one would have produced a *wrong* number, not a missing one: a
`SEQUENCE` member declared as a bare `OBJECT IDENTIFIER` —
`cdpCacheSysObjectID  OBJECT IDENTIFIER,` — matched the definition regex, and
its lazy `(.*?)::=` swallowed everything up to the first real assignment after
the SEQUENCE block. That both deleted `cdpCacheIfIndex` from the parse and
recorded its OID (`cdpCacheEntry 1`) under the name `cdpCacheSysObjectID`,
whose true assignment is `{ cdpCacheEntry 18 }` — verified by reading the
file. The parser now strips `::= SEQUENCE { … }` bodies before matching, the
same treatment IMPORTS got. SEQUENCE members are type declarations, never OID
assignments, so nothing legitimate is lost.

## 2026-08-20 re-verification, and a resolver bug it found

Every OID above was re-resolved from freshly fetched MIBs on 2026-08-20 — all
ten platforms match what ships. The sweep found one bug in `resolve_oid.py`
itself, the same class as the svnVersion incident: a MIB that writes its
IMPORTS on one line ("IMPORTS MODULE-IDENTITY, OBJECT-TYPE, enterprises")
matched the definition regex as a definition named IMPORTS, whose lazy
`(.*?)::=` then swallowed the file's first real assignment. Infoblox's
IB-SMI-MIB is written exactly this way, so `infoblox { enterprises 7779 }` was
eaten and the whole subtree failed to resolve. The parser now strips
`IMPORTS … ;` sections before matching.

## 2026-08-20 second full pass (requested before sign-off)

Everything the scanner asks for was re-verified in one sweep, from files
fetched fresh that day:

* **All ten vendor profiles** re-resolved from the cached vendor MIBs —
  every scalar matches the code (Aruba's AP table including column 34,
  Check Point including `svnServicePack`, Juniper's jnxContents columns,
  F5's build, Blue Coat, Palo Alto, Fortinet, Infoblox, Opengear).
* **Standard MIBs** (SNMPv2-MIB, IF-MIB + ifXTable, IP-MIB both address
  tables, ENTITY-MIB) resolved from the IETF module texts — all 40 objects
  match, including every entPhysicalTable column 2–16 and the
  PhysicalClass 1–12 enum.
* **CISCO-STACKWISE-MIB** — all eight cswSwitchInfoTable columns and the
  full SwitchState enum confirmed from the Cisco module.
* **LLDP-MIB (IEEE 802.1AB)** — lldpRemEntry columns 4–12 and
  lldpLocPortEntry columns 2–4 resolved from the IEEE module;
  `INDEX { lldpRemTimeMark, lldpRemLocalPortNum, lldpRemIndex }` and
  `INDEX { lldpLocPortNum }` confirmed verbatim, as were all three
  subtype/capability enumerations (chassis 1–7, port 1–7, capability bits
  0–7 — and the port numbering really does differ from the chassis one).
* **CISCO-CDP-MIB** — cdpCacheEntry columns 3–9 and
  `INDEX { cdpCacheIfIndex, cdpCacheDeviceIndex }` confirmed;
  CISCO-TC `ip(1)` confirmed for cdpCacheAddressType.
* **IANAifType** — every claimed ifType number matches, with one naming
  correction: 202 is `virtualTg`, not a stack sub-interface type. The
  constant was renamed (`IF_TYPE_VIRTUAL_TG`); Cisco StackSub-St* ports do
  report 202 in the field, and it stays classified virtual either way.
* **The enterprise-manufacturer map** — the audit's only real findings; see
  "Manufacturer identification" above.
