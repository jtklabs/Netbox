# SNMP inventory scanner

Collects hardware inventory over SNMPv3 and writes it into NetBox through the
REST API. Runs on poller boxes near the gear, one poller per site or region.

This is the project's discovery mechanism. It replaced the orb-agent + Diode
stack, which was removed in "Remove Diode and orb-agent: discovery becomes
ours".

## Why this exists

Discovery used to go through NetBox Labs' orb-agent into Diode: thirteen
services whose only job was moving data into NetBox. It derived a device's
model by looking up `sysObjectID` in tables compiled into a Go binary, which
produced device types like `aristaDCS7050SX272Q` and `aruba7010` — the
manufacturer glued onto the model, and neither string is what the vendor calls
the product. Palo Alto came out right only because the binary happened to embed
`lookup_extensions/pan.yaml`.

Correcting a model in NetBox did not stick. Diode matches device types on
manufacturer + model, so the next scan did not recognise the corrected type and
recreated the bad one. The OSS reconciler also applied everything
automatically — the review queue is a commercial feature.

This scanner asks the device instead. ENTITY-MIB `entPhysicalTable` is where a
device reports its own model, serial, and the model and serial of every module
in it. `sysObjectID` is used **only** for the enterprise arc that names the
manufacturer; nothing below that arc is consulted, because that is exactly the
guesswork being removed. Where a device populates no ENTITY-MIB — normal for
firewalls and load balancers — the vendor's own documented scalar OIDs are read.
Where neither answers, the model is left **blank** rather than invented: a
visible gap is better than a plausible wrong answer somebody later has to
un-learn.

Everything downstream of discovery here is already ours (Support Management
serial matching, Hardware Lifecycle EoL), so owning discovery fits.

## What it collects

- System info: hostname, description, location, contact
- Chassis **model** and **serial**, per chassis
- **Modules** and their own serials, into NetBox module bays
- **Interfaces**: name, description, type, speed, MTU, MAC, admin state
- **IP addresses** assigned to interfaces, with real prefix lengths
- **Cisco stacks**: a VirtualChassis with one Device per member, each with its
  own serial and model, positions and master from CISCO-STACKWISE-MIB
- **Software version** for every supported vendor
- **Aruba access points**, from the controller that terminates them
- **CDP/LLDP neighbors**, synced into NetBox as tagged Cable objects — see
  [Cables from CDP/LLDP](#cables-from-cdplldp)

### Vendors

Cisco (IOS, IOS-XE, NX-OS), Arista EOS, Aruba (ArubaOS controllers, ClearPass,
CX), F5 BIG-IP, Palo Alto PAN-OS, Fortinet FortiOS, Check Point Gaia, Infoblox
NIOS, Juniper Junos, Opengear.

An unrecognised vendor still works — it just loses the vendor-specific extras.
The standard MIBs carry model, serial, modules and interfaces for most gear.

Every vendor OID was resolved from the vendor's published MIB rather than
recalled; see [docs/OID-SOURCES.md](docs/OID-SOURCES.md) for the provenance and
[docs/resolve_oid.py](docs/resolve_oid.py) to re-derive them.

### Where the software version goes

NetBox 4.6 has no per-device software version field, so the reading needs a
home. There are two, and the scanner picks whichever the instance can take:

**The Lifecycle plugin, when it is installed.** Its `DeviceSoftware` model
exists for exactly this — it keeps the raw version string, what the reading came
from, when it was taken, and drives compliance against approved versions. The
scanner posts the whole fleet's readings to its ingest endpoint in one call at
the end of a run:

```
POST /api/plugins/refresh/device-software/report/
[{"device": 42, "version": "17.03.04a", "platform": "Cisco IOS-XE",
  "source": "snmp", "raw": "<verbatim sysDescr>"}, ...]
```

The verbatim `sysDescr` goes with it, so a version that looks wrong can be
traced to what the device actually said rather than argued about. That endpoint
also bumps an unchanged version without writing a changelog entry, which is why
a nightly sweep does not bury the changes that matter.

**A custom field otherwise.** The scanner creates a `software_version` text
custom field on `dcim.device` and writes there. No manual setup needed.

Never both — the same fact in two places drifts. Which one is in use is logged
at the start of every run. The choice is made by probing the endpoint, not by
reading the plugin list, because an instance can run a Lifecycle plugin older
than its software models.

Either way the device's **Platform** is also set to the OS family (`Cisco
IOS-XE`, `PAN-OS`, `Arista EOS` …), which is what Platform is for and makes the
fleet filterable by NOS.

Find everything running a given release:

```
/plugins/refresh/device-software/?version=17.03.04a     # with the plugin
/dcim/devices/?cf_software_version=17.03.04a            # with the custom field
```

## Cables from CDP/LLDP

Every scan also reads who the device says it is cabled to — LLDP on
everything, CDP as well on Cisco — and writes confirmed adjacencies into
NetBox as **Cable** objects, so the patching documents itself.

The matching in between is deliberately fussy, because a wrong cable is worse
than a missing one: nobody notices it until they are standing in front of the
rack. Every resolution step either finds exactly one answer or reports and
writes nothing:

- **The neighbor's name is matched with domain-suffix tolerance** (`sw1`
  vs `sw1.corp.example.com`, case-insensitively), then by its chassis MAC
  against interface MACs, then by its CDP management address. Several
  candidates → reported as ambiguous, never picked from.
- **Port names are canonicalised across spellings.** CDP says
  `GigabitEthernet1/0/1`, LLDP usually `Gi1/0/1`, and NetBox holds whatever
  `ifName` said. The documented Cisco long↔short pairs are folded together;
  an unrecognised prefix passes through untouched, so exotic hardware fails
  visibly (an unmatched-port report) rather than matching something wrong.
- **Stack links land on the member.** `Gi2/0/1` on `sw1` terminates on the
  member-2 Device of sw1's virtual chassis, found by searching the whole
  chassis — the name match alone would have put the cable on the master.
- **One link, one cable, from either side.** A Cisco box reports the same
  link over both protocols; both sightings merge before anything is written.
  Scanning A then B converges on a single cable, and rescans create nothing.

Safety rules, all deliberate:

- Scanner cables carry the **`discovered` tag** and stay a class of their
  own — updatable and bulk-removable without touching hand-drawn cabling.
- An adjacency that **disagrees with an existing cable** on either end is
  drift: reported loudly, never rewired.
- A previously discovered cable whose adjacency **disappears** is flagged,
  not deleted — the far end may simply have been down during the scan. A
  device whose neighbor tables answer nothing flags nothing at all, since
  disabled protocols say nothing about cabling.
- The one thing the scanner ever deletes is its own **husks**. NetBox keeps
  a Cable when the interfaces under it are deleted (a decommissioned
  neighbor leaves its cable squatting one-ended on the surviving port, or
  dangling with no ends at all — see docs/API-NOTES.md). A
  `discovered`-tagged husk documents nothing and blocks the port against the
  link that actually exists now, so it is released and logged; a hand-drawn
  cable in the same state is still reported as drift and left for a person.
- A neighbor **NetBox has never heard of** is reported as pending. No device
  is fabricated to terminate its cable.
- **Phones, APs and servers are not cabled by default.** They announce
  themselves as neighbors too, and cabling them is a modelling choice:
  `cable_neighbor_classes = network` cables network gear only (classified
  from the capability bits and the platform string the neighbor reports
  about itself). Add `phone`, `ap`, `host` or `unknown` to widen it.
  Everything excluded is reported, so nothing vanishes silently.

Turn the whole thing off with `sync_cables = false`.

One honest limitation: an unmanaged switch or media converter between two
managed devices is invisible to both protocols, so the two sides see each
other "directly" and the cable drawn says so. That is what the protocols
know, not necessarily what the patch panel does.

`--probe` prints the whole neighbor view first — deduplicated links, the
class each neighbor would be filed under, and how the local port was resolved
— so what a box reports can be inspected before any cable is drawn.

## How a poller decides what to scan

Nothing is swept. The poller asks NetBox which addresses are its own, so adding
a site to a poller's workload is a tag in the UI rather than a config change on
a box somebody has to SSH into.

### The tag scheme

Tag slug `poller-<name>`, applied at **region**, **site** or **device** level.
A poller configured with `name = boston` looks for `poller-boston`.

Precedence, most specific first:

```
device tag   >   site tag   >   region tag
```

- A device tagged `poller-boston` is Boston's even if its site is Dallas'.
- A site tagged `poller-dallas` is Dallas' even if its region is Boston's.
- Regions nest, and the walk goes **upwards** from the site to the nearest
  tagged ancestor — so a sub-region tagged for another poller takes its sites
  back from a parent tagged for us.

"Belongs to another poller" is structural, not a list: any tag whose slug starts
with `poller-` and is not ours claims the object. Standing up a new poller
therefore never requires touching the existing pollers' configuration.

An object tagged for two pollers, one of them us, resolves to us — the
alternative is a device nobody scans.

### Which addresses

Two sources, unioned and de-duplicated:

1. **IPAM addresses** inside a prefix scoped to one of our sites. This is how a
   newly imported address gets scanned before any device exists for it.
2. **Existing devices** at our sites that have a primary IP, so known devices
   get rescanned.

Then devices explicitly tagged for another poller are removed from both.

Optionally restrict source 1 to addresses carrying a `scan` tag
(`scan_tag` in the config; blank scans every address in our prefixes).

### Site membership comes from the prefix

An address's site is **not** a column anywhere. It comes from the prefix the
address falls inside, because prefixes are already scoped to sites in NetBox.
One indirection gives the poller both the site for a brand-new device and,
through the site's tags, whether the address is its to scan.

**Consequence: create your prefixes and scope them to sites before importing
addresses.** Addresses with no containing prefix are imported but no poller
will select them, and `import_ips.py` says so loudly when that happens.

## Install on a poller

Needs Python 3.9+, `requests`, and net-snmp's command-line tools.

```bash
sudo apt install snmp python3-requests      # Debian/Ubuntu
sudo dnf install net-snmp-utils python3-requests   # RHEL family
```

Then copy this directory to the poller and configure:

```bash
cp snmp-inventory.conf.example snmp-inventory.conf
cp snmp-credentials.conf.example snmp-credentials.conf
chmod 600 snmp-credentials.conf
$EDITOR snmp-inventory.conf snmp-credentials.conf
```

If `requests` is not packaged for your distro, `pip install -r requirements.txt`
into a venv.

### Credentials

SNMPv3 only. v2c sends a community string in clear text, and these credentials
cross the production network on every scan.

`snmp-credentials.conf` holds one section per credential set. They are tried
**in file order** and the first a device accepts is used, so put the set that
covers most of the fleet first — every set tried before it costs a round trip.

```ini
[credential:primary]
security_name = netops
auth_protocol = SHA-256          ; MD5 | SHA | SHA-224 | SHA-256 | SHA-384 | SHA-512
auth_passphrase = ...
priv_protocol = AES              ; DES | AES | AES-192 | AES-256   (AES = AES-128)
priv_passphrase = ...

[credential:legacy-sha1]
security_name = netops
auth_protocol = SHA
auth_passphrase = ...
priv_protocol = DES
priv_passphrase = ...
```

The security level is derived from which passphrases you set — both gives
`authPriv`, auth alone gives `authNoPriv`. Set `security_level` explicitly only
to override that. `context` is available for VRF-aware platforms.

**Do not quote passphrases.** Everything after the `=` is taken literally, so
`auth_passphrase = "hunter2"` sets the passphrase to `"hunter2"` with the quotes
in it. `%`, `#`, `$` and spaces inside a passphrase are all fine unquoted; the
only thing you cannot express is a trailing space, which gets stripped.

If the device answers but rejects you, that surfaces as an authentication
failure and the next credential set is tried. A **timeout** means nothing came
back at all — the address, an ACL, or SNMP not being enabled, not the
passphrase. `--probe` is the quickest way to tell those apart.

**Passphrases never reach the command line.** Anything in `argv` is world
readable through `ps` for as long as the process runs. Each credential set is
written to a private `snmp.conf` (mode 0600, in a 0700 temp directory) and
net-snmp is pointed at it with `SNMPCONFPATH`, which also stops the operator's
own `~/.snmp/snmp.conf` from silently changing which credentials a scan used.
There is a test that watches the process list during a live scan to keep this
honest.

The NetBox token can live in the config or, better, in `NETBOX_TOKEN` — the
environment wins, so a systemd unit or secrets agent can supply it without the
token ever being on disk.

## Onboarding a single device from NetBox

For adding one device rather than importing a list, the **Discovery** plugin
(`plugins/netbox-discovery/`) puts a form in NetBox: someone types the
management IP and nothing else.

The address is enough because of the same chain the sweep uses, run backwards:

```
address  ->  most specific containing prefix
         ->  that prefix's site
         ->  the site's (or nearest region's) poller-<name> tag
         ->  the poller that will scan it
```

If the address falls in no prefix, or the prefix has no site, or nothing is
tagged, the form **refuses it there and then** and says which of those to fix.
Accepting it would mean a request sitting in a queue that nothing will ever
service.

Then:

1. The poller picks the job up on its next check-in and scans the device.
2. If nothing about the result is questionable, it is **applied there and
   then** — the poller already has the reading, so no second walk and no second
   check-in. Enter an IP, come back to a device.
3. Otherwise it stops in *Awaiting review* with the reason, and waits for a
   person. They can override the name, site or role, then apply.

### What counts as questionable

Review is spent only where it buys something. Reviewing everything sounds safer
and is not — it teaches people to click Apply without reading, which is worse
than not asking.

| Held for review | Why |
|---|---|
| No model reported | There is no device type to create, and inventing one is the habit this tool exists to break |
| No site | Nothing placed the address, so there is nowhere to put the device |
| Serial already on another device | Either the box was onboarded twice, or a serial is wrong. Both need a person |
| Hardware changed since review | The approval was for a specific box and this is no longer it |
| Scan failed | Nothing was learned — see manual entry below |

Set `review_policy` to `always` in the plugin config to go back to reviewing
every device.

### Devices SNMP cannot reach

Plenty of gear has no SNMP, has it switched off, or sits behind something that
will not pass it — and it still belongs in the inventory. When a scan fails,
the request offers **Enter the details by hand**: name, manufacturer, model,
serial, platform, role. That builds the same record shape a poller would have
reported, so the same apply creates it — one route into DCIM, not two.

Hand-entered requests are flagged `manually_entered` and labelled as such
wherever they are shown. A typed serial and an observed one are not equally
trustworthy and should never look alike.

Also available over the API:

```bash
curl -sX POST "$NB/plugins/discovery/onboarding-requests/12/manual/" -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"name": "core-sw-09", "manufacturer": "Cisco",
       "model": "WS-C2960-24TT-L", "serial": "FOC1234ABCD"}'
```

### Overlapping address space

Address space repeats across companies that have been acquired: two prefixes of
`10.10.1.0/24` can and do coexist, because NetBox does not enforce uniqueness in
the global table. A containment lookup returns both, so an address alone is not
always a key.

The onboarding form therefore takes an optional **tenant** (with a tenant-group
picker to make the list usable) and an optional **VRF**. Neither is asked for up
front in the normal case — most addresses resolve without them.

The rule is: **if the candidate prefixes span more than one tenant and nobody
said which, the request is refused and the candidates are listed.** Guessing is
the one thing that must not happen; picking wrong files an acquired company's
switch under your site, or hands it to a poller with no route to it.

```
This address is inside prefixes belonging to 2 different tenants, so there is
no way to tell which device it is. Choose a tenant. Candidates:
  10.10.1.0/24 (tenant Company A, VRF global, site Boston DC1);
  10.10.1.0/24 (tenant Company B, VRF global, site CompanyB HQ)
```

Where one tenant owns all the candidates, the most specific wins as usual and
nobody types anything. The resolved tenant is inherited from the prefix and
**stamped onto the created device and its IP**, so ownership carries through
rather than being used for routing and then thrown away.

Note that a tenant *group* is not enough on its own: if two acquired companies
sit in the same group and both use `10.10.1.0/24`, the group does not
distinguish them. The group only filters the tenant list.

Pollers can carry a tenant too, but it is not how work is routed — that still
follows from the prefix's site. It is a guard: a request for another tenant
arriving at a poller almost certainly means a site is tagged for the wrong one.

### When no prefix matches

An address in no prefix has no site, but it is still worth looking at. The
`default_region` plugin setting (default `us`) names a region whose
`poller-<name>` tag supplies a fallback poller, so the scan goes ahead and the
request arrives in review marked *no prefix matched*. **A site must be chosen
before it can be applied** — the approve path refuses without one — so nothing
lands in the wrong place. Set `default_region` to `""` to switch the fallback
off and have unmatched addresses refused instead.

### Running the queue

Run it on a short timer, separately from the nightly sweep — somebody is sitting
in NetBox waiting for it. Every minute is reasonable:

```
* * * * * flock -n /var/lock/snmpinv-onboard.lock /opt/snmp-inventory/snmp_inventory.py --config /opt/snmp-inventory/snmp-inventory.conf --onboard --quiet
```

`flock -n` matters at that cadence: a scan of a slow device takes longer than a
minute, and without it the runs pile up. Nothing is scanned twice either way —
check-in claims work under a row lock — but processes would accumulate.

An idle check-in is **one API request** returning `{"jobs": []}`, and it writes
no changelog entry, so a fleet of pollers polling every minute costs very
little. A systemd timer works as well and needs no lock, since systemd will not
start a second copy of a unit already running.

`--onboard` replaces the sweep for that run; it does not scan anything else.

**The two are separate queues, and this catches people out.** A request added
through the onboarding form is an `OnboardingRequest`, not an IPAM address, so
a plain sweep will never see it however correctly its site and poller resolved
— you need `--onboard`. Conversely `--onboard` only works the queue and ignores
your tagged sites entirely. Symptom of using the wrong one: the sweep logs
`poller poller-x owns 1 of N sites` and then finds nothing.

The poller's **name** must match too, and it is the *bare* form: a site tagged
`poller-checkmk-us` files its requests under a poller called `checkmk-us`.
Either form is accepted — check-in strips a leading `poller-` — but the Pollers
page is the place to confirm it: two rows differing only by that prefix means
an older poller registered under the wrong one and is holding no work.

### When a request stops because a prefix was missing

The common one. An address falls outside every prefix, the default region
still supplies a poller so the device is scanned anyway, and it stops for
review saying there is nowhere to create it. You create the prefix — now what?

**Re-check IPAM**, on the request's detail page. It re-runs resolution and
keeps the scan, because the device answered perfectly well and only IPAM was
missing. If nothing else about the reading needs a decision it goes straight to
approved, and the next check-in creates the device.

The other two buttons are for different problems, and picking the wrong one
costs you the scan:

| button | when | the scan |
|---|---|---|
| **Re-check IPAM** | IPAM was wrong, the device was fine | kept |
| **Scan again** | the device's reading is wrong or stale | discarded, re-walked |
| **Try again** | it never got scanned — failed or unresolved | n/a |

Do **not** reach for `--host` to fix this. That runs the sweep, which writes
the device straight into DCIM and never touches the queue, so the request stays
in review for ever and you end up with a device nobody's onboarding record
points at. If you have already done it, Re-check IPAM still resolves the
request — applying is idempotent by serial, so the poller adopts the device
that is already there rather than making a second one.

All three are on the API too:

```bash
curl -sX POST "$NB/plugins/discovery/onboarding-requests/12/recheck/" -H "$AUTH"
curl -sX POST "$NB/plugins/discovery/onboarding-requests/12/retry/"   -H "$AUTH"
```

### The same workflow over the API

Everything the form and the buttons do is available over REST, so onboarding
can be driven from a provisioning script or a ticket system. The API and the UI
call the same functions, so they accept and refuse exactly the same things.

```bash
NB=https://netbox.example.com/netbox/api
AUTH="Authorization: Bearer nbt_..."

# Add — the address is the only required field.
curl -sX POST "$NB/plugins/discovery/onboarding-requests/" -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{"address": "10.10.1.5"}'
# -> 201 with the resolved site and poller, or 400 saying which prefix is missing

# Review — read what the poller found.
curl -s "$NB/plugins/discovery/onboarding-requests/12/" -H "$AUTH"
# -> status "review", discovered.devices[] with model, serial, version, members

# Approve — empty body means "as scanned".
curl -sX POST "$NB/plugins/discovery/onboarding-requests/12/approve/" -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{}'
# -> 200 status "approved"; 409 if it is not awaiting review

# Approve with overrides
  -d '{"override_name": "core-sw-01", "override_site": 3, "role": 7}'

# Reject, with a reason that stays on the record
curl -sX POST "$NB/plugins/discovery/onboarding-requests/12/reject/" -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{"reason": "decommissioned"}'

# Retry a failed one, re-running resolution
curl -sX POST "$NB/plugins/discovery/onboarding-requests/12/retry/" -H "$AUTH"
```

Useful filters: `?status=review`, `?needs_attention=true` (anything waiting on
a person rather than on a poller), `?poller=boston`, `?site_id=3`.

The poller's own three calls are on the same API — `pollers/check-in/`,
`onboarding-requests/{id}/scanned/` and `.../applied/` — which is all
`--onboard` uses. Nothing about the poller is privileged; it is just another
API client.

Two behaviours worth knowing:

- **What the scan found is stored in full**, on the request itself
  (`discovered`) — every interface with its address, MAC, MTU, speed and
  description, every module with its serial. About 5 KB for a 48-port stack.
  The review page shows only counts and the member breakdown, but the rest is
  kept so the stored reading is enough to build the device from.
- **Apply re-reads the device**, because a preview can be hours old and the
  device is right there to ask. If the serial or model has changed since the
  review, the request goes *back* to review rather than applying — the person
  approved a specific box and this is no longer that box.
- **If the device is unreachable at apply time, the reviewed reading is used
  instead**, and the poller says so in its log. Somebody already approved that
  exact reading; making them start over because a switch was rebooting would be
  worse. A request that was never successfully scanned still fails honestly —
  there is nothing to fall back to.
- **Pollers register themselves.** The first check-in creates the poller record.
  The UI shows when each last checked in, so a request that has not moved is
  visibly waiting on a poller rather than mysteriously stuck.

## Asking one device what it is

Before pointing this at a fleet — or when a model comes back wrong — probe a
single address. It needs **no NetBox**: no URL, no token, no tags, no prefixes.
A credentials file is the only input.

```bash
./snmp_inventory.py --credentials snmp-credentials.conf --probe 10.10.1.5
```

It prints what the device said and what that would become, side by side:

```
SYSTEM          name, description, uptime, sysObjectID (as a vendor, never a model)
IDENTIFICATION  vendor profile, manufacturer, platform, software version
CHASSIS         every entPhysicalTable chassis row: model, serial, hw/sw rev
STACK           CISCO-STACKWISE-MIB members, roles and states, decoded
MODULES         bay, model, serial
INTERFACES      name, the NetBox type it would get, speed, MTU, MAC, alias, addresses
ACCESS POINTS   for a wireless controller, everything it terminates
WHAT THIS WOULD BECOME IN NETBOX
                the devices, virtual chassis and modules that would be created
```

That last section is the point. A model arriving wrong is nearly always
ENTITY-MIB being empty rather than the modelling being confused, and having
both in one view says which immediately. A device that reports no model is
called out as `NO MODEL — this device would be skipped` rather than quietly
producing nothing.

Two extras:

```bash
--json                    the same findings as structured data, for piping
--save-walk FILE          also capture the raw walk as a test fixture
```

`--save-walk` writes the recorded-walk format the emulator and the parsing
tests read, captured with whichever credential set actually worked. Drop it in
`tests/fixtures/` and the whole suite runs against your hardware instead of my
synthetic devices — which is the single best thing you can do to make these
tests mean something for your fleet.

## Importing your existing address list

`import_ips.py` takes a CSV. `address` is the only required column:

```csv
address,dns_name,description
10.10.1.5,core-sw-01.example.net,Building A core stack
10.10.1.6,,Building A access stack
10.10.1.20/24,fw-edge-01.example.net,Palo Alto edge pair
```

A mask is optional — without one the mask of the containing prefix is used, so
the address is stored the way NetBox expects rather than as a `/32`.

```bash
./import_ips.py --config snmp-inventory.conf --csv devices.csv --dry-run
./import_ips.py --config snmp-inventory.conf --csv devices.csv
```

Idempotent: re-importing skips addresses that already exist, and only adds the
scan tag if an earlier import predates it. It never removes tags.

## Running a scan

Always start with `--dry-run`. It performs the full scan and prints every object
it would create or change, and writes nothing.

```bash
./snmp_inventory.py --config snmp-inventory.conf --list-targets   # what would be scanned
./snmp_inventory.py --config snmp-inventory.conf --dry-run
./snmp_inventory.py --config snmp-inventory.conf
```

Useful flags:

### Concurrency

Both the sweep and the onboarding queue scan `workers` devices at a time
(default 8, in `[snmp]`). The walks are subprocesses waiting on the network, so
this scales close to linearly — measured against 8 emulated devices:

| workers | elapsed |
|---|---|
| 1 | 4.99s |
| 4 | 1.28s |
| 8 | 0.69s |

NetBox writes are serialised behind a lock in both paths. That is deliberate:
the walks are the slow part, and two devices being written at once would race
to create the same manufacturer or device type, one of them losing to a 400.

Raise `workers` for a big site. The limit is the poller's file descriptors and
how much SNMP the devices will tolerate at once, not CPU.

| Flag | Effect |
|---|---|
| `--host ADDR` | scan one address instead of asking NetBox (repeatable) |
| `--site-id N` | site for `--host` results when it cannot be derived |
| `--collect-only` | scan and log findings, never touch NetBox |
| `--new-only` | only IPAM addresses, skip rescans of known devices |
| `--limit N` | scan at most N targets |
| `-v` | debug logging, including which credential set each device accepted |

Once it looks right, run it from cron or a systemd timer:

```
17 3 * * *  cd /opt/snmp-inventory && ./snmp_inventory.py --config snmp-inventory.conf --quiet
```

### Two schedules

The sweep and the onboarding queue answer different questions and want
different cadences:

```cron
# Onboarding queue — someone is waiting, so run it often. One request when idle.
* * * * *   flock -n /var/lock/snmpinv-onboard.lock /opt/snmp-inventory/snmp_inventory.py --config /opt/snmp-inventory/snmp-inventory.conf --onboard --quiet

# Full re-scan — catches everything that changed on its own.
7 */6 * * * flock -n /var/lock/snmpinv-sweep.lock /opt/snmp-inventory/snmp_inventory.py --config /opt/snmp-inventory/snmp-inventory.conf --quiet
```

Six-hourly is a sensible sweep: it picks up code upgrades, new line cards,
re-cabled interfaces and swapped hardware without hammering the fleet. Nothing
is written unless a value actually differs, so a sweep over an unchanged estate
produces no changelog entries at all — which is what makes running it four
times a day reasonable.

Use a separate lock from the onboarding job so a long sweep never blocks
onboarding; they are independent and can overlap safely.

### When a serial is already on another device

Devices are matched by serial first, which is what makes a renamed or
re-addressed box resolve to the record it already has. It is also what would
let a duplicated or mistyped serial pull a scan onto the *wrong* record and
overwrite it — silently, since nothing about that looks like an error.

So the scan is refused instead. Nothing in DCIM is touched, and the collision
is raised as a **Discovery Issue** with both sides of it: the address scanned,
the name it reported, the serial, and the record it collided with.

Telling a genuine conflict from a normal change comes down to what else agrees:

| Serial matches, and… | Read as | Action |
|---|---|---|
| same name | re-addressed box | sync normally |
| same address | renamed box | sync normally |
| neither matches | two devices, one serial | **refused and raised** |

One open issue per address and serial — the sweep runs four times a day and
would otherwise file the same complaint until somebody dealt with it. Mark an
issue *Ignored* if the duplicate is expected and should stop being raised.

Onboarding applies the same rule earlier: a scan whose serial is already in
NetBox stops for review rather than creating a second device for hardware that
already exists.

### When a serial changes

A rescan finding a different serial under a name we already knew means the
metal was swapped — an RMA, a spare off the shelf, a replaced line card. The
serial is **never overwritten in place**, because serials are what support
contracts and quotes are matched on and losing one silently loses the thread on
a box that may still be under contract.

For a **chassis**, the old Device record is kept:

- renamed to `<name> [replaced <old-serial>]`, freeing the name
- status set to `inventory` (configurable via `retired_device_status`)
- tagged `replaced`, and its primary IP cleared so it is not rescanned
- the new unit is created and takes the name and the address

For a **module**, the old row cannot be kept — NetBox requires a module to sit
in a bay, and the bay is being refilled — so the swap is recorded *before* the
serial is overwritten.

Either way a **Hardware Replacement** record is written, with both serials, the
model, the bay for a module swap, and a link to the retained device for a
chassis swap. That is the queryable history: *"every serial that changed in the
last quarter"* is one filter, which is the form the question actually gets asked
in when reconciling contracts. NetBox's changelog holds the old value too, but
only as a diff on one object at one moment.

Set `retain_replaced_hardware = false` to go back to overwriting in place.

### What it writes

Auto-created when missing: manufacturer, device type, module type, module bay,
device, virtual chassis, interface, IP address, platform, device role, and the
`software_version` custom field.

Idempotent throughout — objects are looked up by natural key before being
created, and existing ones are patched only where a field actually differs, so
a rescan produces no duplicates and no changelog noise.

Three deliberate behaviours:

- **The device's reported model wins.** If NetBox holds a different device type,
  the device is moved to the one matching what the hardware reported, and the
  correction is logged. This is the opposite of the Diode behaviour described
  at the top.
- **A fact we did not collect never blanks one somebody entered.** Absent is not
  the same as empty.
- **A device found at the wrong site follows its address.** Devices are matched
  by serial first, and a serial is site-independent, so a re-racked unit would
  otherwise sit at its old site forever — and a stack would pick up its new
  members at the new site while the known one stayed behind, leaving a virtual
  chassis spanning two sites. The move is logged. Turn it off with
  `move_devices_between_sites = false` if you place devices by hand.

## Testing without any real devices

The lab runs emulated devices: a real `snmpd` doing real SNMPv3 — engine
discovery, SHA-256/AES USM, the actual wire format — with a `pass_persist`
backend replaying a recorded walk. From the scanner's side there is no
difference between one of these and a switch.

```bash
python3 tests/emulator/run_emulator.py
```

That brings up every fixture, one UDP port each on loopback, and prints the
shared credentials. Point the scanner at one:

```bash
./snmp_inventory.py --config snmp-inventory.conf \
    --host 127.0.0.1:11610 --site-id 3 --dry-run
```

(The `host:port` form goes straight to net-snmp, so no privileged port is
needed.)

### The fixtures

`tests/fixtures/*.walk` are stored in exactly the format
`snmpwalk -On -Oe -Ot` prints, so **a capture from a real device replaces a
synthetic one with no conversion**:

```bash
snmpwalk -v3 -l authPriv -u netops -a SHA-256 -A ... -x AES -X ... \
    -On -Oe -Ot 10.10.1.5 1.3.6.1 > tests/fixtures/my-real-switch.walk
```

Be honest about what they prove. The shipped fixtures are **synthetic** — built
from the structure each vendor's MIB defines and the values that family is
documented to return. They prove the scanner handles the shapes correctly. They
cannot prove a given firmware populates a given table; only a real capture does
that. Replacing them with real captures as devices become available is the
intended path, and `tests/emulator/make_fixtures.py` regenerates the synthetic
ones if you need to adjust a shape.

One emulator limitation, documented where it bites: net-snmp's `pass_persist`
protocol is line-based, so a value containing newlines is flattened to spaces on
the way out. Real devices do return multi-line strings — Cisco's `sysDescr` is a
paragraph — so that case is covered against recorded text instead, in
`tests/test_parsing.py`.

### Running the tests

```bash
python3 -m pytest tests/ -q
```

Three tiers, each unlocked by what the machine can do — nothing is silently
skipped without saying why:

| Where | Passes | What runs |
|---|---|---|
| Any machine with Python | 81 | parsing, collection, modelling, ownership |
| + net-snmp 5.8 or newer | 100 | the above, plus the emulated devices |
| + a NetBox to write to | 122 | the above, plus the live instance |

The two axes are independent: the live-NetBox tests drive the sync layer from
recorded walks rather than an emulated device, so they need a NetBox but not
net-snmp. Tying them together would have made them unrunnable on a Mac, which
is where the NetBox dev stack actually lives.

The middle tier needs net-snmp 5.8+ because the emulated devices authenticate
with SHA-256/AES. macOS ships 5.6, which offers only MD5 and SHA-1 and whose
`/usr/sbin/snmpd` is a launchd stub, so those tests skip there and run on a
Linux poller.

Add the live tier by pointing it at an instance you do not mind writing to. It
creates everything under an `SNMPINV_TEST_` prefix and tears it down
afterwards, and never deletes anything it did not create:

```bash
export SNMPINV_TEST_NETBOX_URL=http://10.50.10.132:8080/netbox
export SNMPINV_TEST_NETBOX_TOKEN=nbt_...
python3 -m pytest tests/ -q
```

The split is deliberate:

| Layer | Tested against | Why there |
|---|---|---|
| Parsing | recorded net-snmp output | fast, and covers what the wire cannot carry |
| Collection & modelling | fixtures, no network | a failure points at the code, not the lab |
| Ownership precedence | in-memory NetBox | many topologies, each cheap to build |
| Wire path | emulated devices | credentials, GETBULK, real net-snmp output |
| Query and write shapes | a live NetBox | the half a fake can never check |

That last row earns its keep. The live tests caught a bug that a fake never
could: ownership resolution fetched regions with `brief=0`, and NetBox switches
to the brief serializer on the parameter being *present* whatever its value — so
regions arrived with no `tags` and no `parent`, and region-level poller tags
were silently ignored everywhere. The emulator caught three more, including
`-Cr 25` being rejected by net-snmp (the value must be attached: `-Cr25`), which
had been breaking every bulk walk.

## "No response" from a device you can poll by hand

Almost always **GETBULK**, not credentials and not reachability.

A walk uses `snmpbulkwalk`, which asks for `max_repetitions` (25) varbinds in
one request. The reply carries all of them at once, and once it exceeds the
path MTU it gets fragmented — which plenty of firewalls, and some device CPUs,
drop outright. Nothing comes back, so it looks identical to an unreachable
host. When you poll by hand you almost certainly use `snmpwalk`, which is
GETNEXT: one varbind per packet, small replies, never trips over this.

The giveaway is that it **is not model-specific**. It depends on how much data
the device has, so two identical switches differ if one has longer interface
descriptions or more `sysORTable` rows.

The scanner handles this by itself. Before any walk it sends a single GET,
which is one small packet each way and so cannot fail for size reasons. If that
GET is answered but a GETBULK is not, the request size is **stepped down** until
the device answers:

```
25  ->  10  ->  4  ->  GETNEXT
```

```
WARNING 10.10.1.5 did not answer a GETBULK of 25 at 1.3.6.1.2.1.2 but does
answer GETs — its replies are too big for the path. Retrying at 10 for this
device.
```

Stepping down rather than dropping straight to GETNEXT matters because devices
overshoot by different amounts. Something that cannot manage 25 will usually
manage 10, which is still ten times fewer round trips than GETNEXT — on a
48-port stack that difference is most of the scan.

Whatever it settles on latches for the rest of that device's scan, so the cost
is one timeout per step, once per device, not once per table.

### It only pays that once

The limit each device settled on is written to `bulk_state_file`
(`/var/lib/snmp-inventory/getbulk-limits.json` by default) and reused on the
next run, so a six-hourly rescan does not rediscover it every time. A device
that manages the full size is deliberately *not* recorded — that keeps the file
to the exceptions, and means raising `max_repetitions` later actually reaches
the devices that were always fine.

```json
{
  "10.10.1.5":  { "max_repetitions": 10, "measured_at": 1786000000.0 },
  "10.10.9.200": { "max_repetitions": 0,  "measured_at": 1786000042.0 }
}
```

`0` means the device answered no GETBULK at all and goes straight to GETNEXT —
which, being cached, now costs no timeout at all.

Entries expire after `bulk_state_ttl_days` (7) so a device stops being scanned
pessimistically once somebody raises an MTU or fixes a firewall; the next scan
after that re-measures and either confirms the limit or lifts it.

The file is a pure cache. Deleting it costs one slow scan and nothing else, a
corrupt or unwritable one is logged and ignored rather than failing the run,
and it is written atomically so a killed poller cannot leave a half-written
file behind. `--no-bulk-cache` ignores it for one run.

To skip the discovery entirely, `--no-bulk`, or `use_bulk = false` under
`[snmp]` to make it fleet-wide.

If the **first GET** times out too, it really is silence: wrong address, an
ACL, SNMP not enabled, or a firewall. Credentials being wrong looks different
again — the device answers and rejects you, which is reported as an
authentication failure, and the next credential set is tried.

A slow device is worth ruling out separately: the defaults are `timeout = 5`
and `retries = 1`, i.e. two attempts, where `snmpwalk` by hand defaults to six.
Raise `retries` under `[snmp]` if a device is merely lossy rather than large.

## A device that reports no model

Some platforms publish no model over SNMP at all. Cisco Firepower Threat
Defense is the confirmed one — a Firepower 2120 has no `entPhysicalModelName`,
and the model appears nowhere else in its walk either. NetBox needs a device
type, and this scanner will not derive one from `sysObjectID`, so there is
nothing to create the device from.

The request stops at review saying so, and you **type the model there**. Only
that one field: the serial, software version, interfaces and addresses the scan
found are all kept, which is the difference between this and entering the
device by hand.

What the device reports always wins, so an override left on a request cannot
replace a model a later scan manages to read.

**It is needed once.** Once the device exists, a rescan that reads no model
keeps the device type it already has and goes on writing everything else — the
model is only needed to *choose* a device type, which is a question the first
write answers.

Before assuming a platform is in this category, run `--probe`. When nothing
produces a model it dumps every `entPhysicalTable` row, which usually shows the
model sitting in a field this scanner is not yet reading — a one-line fix, and
better than typing it by hand forever. Firepower is the case where the dump
genuinely comes up empty.

## Notes and gotchas

Full detail in [docs/API-NOTES.md](docs/API-NOTES.md). The ones most likely to
bite:

- `?tag=<unknown-slug>` is a **400**, not an empty list. Check the tag exists
  first.
- Repeated `?tag=` parameters **AND** together; there is no OR form.
- `?contains=` returns every containing prefix **least specific first** — sort
  by mask length and take the longest, or a `/16` aggregate wins over the `/24`
  that is actually the site.
- MAC addresses are their own model in NetBox 4.x and **creates are not
  deduplicated**; `interface.mac_address` is read-only.
- On stock Debian/Ubuntu net-snmp ships **without the IETF MIBs**. The scanner
  uses numeric OIDs everywhere and sets `MIBS=""`, so this does not matter — but
  it is why `snmptranslate` is useless on a poller.

## Layout

```
snmp_inventory.py            scan CLI
import_ips.py                CSV -> NetBox IPAM
snmpinv/
  mibs.py                    numeric OIDs, ifType -> NetBox interface type
  vendors.py                 per-vendor OIDs, version extraction, Aruba APs
  snmp.py                    net-snmp subprocess wrapper, snmp.conf, parsing
  collect.py                 walks a device into structured facts
  model.py                   facts -> NetBox-shaped records (stacks, modules)
  neighbors.py               CDP/LLDP sightings -> adjacencies (dedupe,
                             canonical port names, neighbor classes)
  selection.py               which addresses this poller owns
  netbox.py                  REST client: lookup-or-create, dry-run
  sync.py                    idempotent writes
  cables.py                  adjacencies -> tagged Cable objects, safely
  config.py                  config and credential files
tests/
  emulator/                  snmpd-backed device emulator + fixture generator
  fixtures/                  recorded walks
docs/
  OID-SOURCES.md             provenance for every OID
  API-NOTES.md               verified NetBox 4.6 behaviour
  resolve_oid.py             re-derive OIDs from vendor MIBs
```
