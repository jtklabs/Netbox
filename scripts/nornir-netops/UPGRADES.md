# Software upgrades: Catalyst IOS XE, Arista EOS and BIG-IP

`configure.py upgrade` uses this directory's shared `.env`, AWS Secrets Manager
login resolution, NetBox inventory/filtering, threaded Nornir runner and private
JSON archives. It is read-only unless `--apply` is present. NetBox is the default
inventory for this command, even if standards deployment defaults to CSV.
Explicit `--csv` and `--ip` still work for lab runs. The profile's image
filename selects the driver: a `.bin` package is Catalyst IOS XE (this section),
an `EOS-<release>.swi` is [Arista EOS](#arista-eos-upgrades) over SSH and a
`BIGIP-<version>.iso` is [BIG-IP](#big-ip-upgrades) over iControl REST.

The IOS XE driver supports **Cisco C9350 IOS XE** profiles and the existing C9300-family
profiles through the IOS XE install workflow. C9350 uses `cisco9k_iosxe` images
(or explicitly selected `cisco9k_iosxe_npe` images); C9300 uses `cat9k_iosxe`.
Wrong-family images and profiles mixing these two families are rejected.
C9350 starting releases must be at least 17.18.1; C9350-24HX and C9350-48HXN
require at least their introductory release, 26.1.1a. C9300 starting releases
must be at least 16.6.2. These minimums do not approve an upgrade path: the exact
PIDs and tested starting/target releases still come from your profile. Other
families, legacy `request platform software` upgrades, ISSU and xFSU are not
implemented. No production release path is approved by the example or tests.

## Run a preview

From `scripts/nornir-netops`, install the dependencies as in the main README,
including `requests` for NetBox and `boto3` if using Secrets Manager. Copy
`upgrade-profile.yaml.example` to a local profile and fill in the exact PIDs,
validated starting versions, target version, filename and Cisco checksum.
The placeholder checksum intentionally prevents using the example unchanged.

```bash
python configure.py upgrade --profile campus-upgrade.yaml \
  --netbox-filter site=atl --netbox-filter role=access-switch \
  --workers 3

# Further narrow the same selection to the IOS XE platform:
python configure.py upgrade --profile campus-upgrade.yaml \
  --netbox-filter site=atl --netbox-filter role=access-switch \
  --netbox-filter platform=cisco-ios-xe --limit atl-access-01
```

All existing NetBox selectors work, including repeated filters, `--filter`,
`--limit`, poller ownership/autofilter and NetBox credentials from AWS. When
the discovery plugin serves redundancy groups, a target set holding more
members of a group than may upgrade at once is refused unless
`--ignore-groups` is passed; see [SCHEDULED_UPGRADES.md](SCHEDULED_UPGRADES.md#redundancy-groups-dependencies-and-holds).
Device login comes from the same `NET_USER`, `NET_PASS`, `NET_ENABLE`,
`NET_AWS_SECRET`, `NET_AWS_REGION` and optional custom JSON-key settings used by
standards deployment. `--env-file` selects an alternate shared file.

Dry run connects, collects the baseline, identifies every stack member's model,
current version and actual running mode from `show version`, checks stack
readiness, confirms committed starting software in install mode, compares
running/startup configuration, checks member flash and verifies an existing
image's MD5. It archives the commands it would execute. It does not copy images,
save configuration, change boot variables, remove packages or reload.
Progress webhooks and local report writes occur during dry run too.

Already-current devices still get a baseline and become `already_current`
without image staging or boot changes. Unexpected starting releases,
mixed stack versions/modes, unapproved PIDs, incomplete critical output and
insufficient flash produce `blocked` with reasons. Unsaved running-configuration
changes are recorded and flagged rather than blocked; see
[Saving the running configuration](#saving-the-running-configuration).

`BUNDLE` is explicitly flagged in the plan and progress feed. A conversion may
run only when `bundle_conversion_validated: true` is set for the profile.
It combines the approved upgrade and conversion in one install/reload.
A device already on the target release but in bundle mode is blocked; that
same-version conversion needs a separately validated procedure.

## Apply the approved path

```bash
python configure.py upgrade --profile campus-upgrade.yaml \
  --netbox-filter site=atl --netbox-filter role=access-switch \
  --workers 3 --apply
```

`--apply` authorizes the disruptive change without another interactive prompt.
The same invocation always collects a fresh baseline; an earlier dry-run report
is not treated as permission to use stale facts. Each NetBox management address
represents a standalone switch or one entire stack. `--workers` defaults to 3
and bounds concurrent device workflows; it does not group redundant switches
or enforce site maintenance order. Choose inventory scopes accordingly.

For a missing image, optional `image_source` lets the switch copy it from a
reachable HTTP(S)/TFTP server to active flash during apply. The URL must have
the exact image basename and no embedded credentials or query parameters.
Without a source URL, pre-stage the image on active flash. Dry run records
`pending_transfer` when the image is missing but a source is configured; source
reachability, checksum and final space remain unverified until apply copies it.
The script never automatically deletes old software to free space.

## Saving the running configuration

`--apply` runs `write memory` as its first step after connecting, before the
running and startup configurations are read and compared. The upgrade persists
the running configuration ahead of the install in any case, so unsaved changes
are saved rather than treated as a blocker, and the comparison then confirms
that the save took. This is the one device write that happens before image
verification and, for scheduled jobs, before the NetBox `ready` authorization:
a device that is later blocked has still had its running configuration saved.
If the two configurations still differ after the save, the device is blocked
and the normalized difference is recorded in `upgrade_plan.saved_config_diff`.

A dry run performs no save. It records the same diff, emits the
`unsaved_changes` stage, and notes in `dry_run_complete` that apply will save
first. Already-current devices are saved by `--apply` as well, then left alone.

`--allow-config-mismatch` continues past a difference that remains after the
save. The device is not blocked, and the running/startup equality checks after
boot preparation and after the upgrade are skipped; every other check, including
the saved autoboot settings, still applies. The diff is still recorded, the
`unsaved_changes` stage says the override was used, and
`upgrade_plan.config_mismatch_overridden` is set. Read the diff first and use
the flag only when you know why the two dumps differ.

## Pre-stage the installer only

Set the switch-reachable download URL in your upgrade profile:

```yaml
image_source: http://images.example.com/cisco9k_iosxe.17.18.04.SPA.bin
```

Then use `--stage-only` with the same inventory selectors:

```bash
# Preview image presence, checksum, flash space and any required copy:
python configure.py upgrade --profile campus-upgrade.yaml --stage-only \
  --netbox-filter site=atl --netbox-filter role=access-switch

# Copy missing installers now, without installing or reloading:
python configure.py upgrade --profile campus-upgrade.yaml --stage-only --apply \
  --netbox-filter site=atl --netbox-filter role=access-switch --workers 3
```

This mode runs only model/version/stack and image-readiness checks; it skips the
NAC, routing and configuration baseline. It retains the approved PID/starting
version (or already-target version), consistent stack and flash-space gates.
Bundle mode is reported but does not require conversion approval to stage a file.

An existing installer is checksum-verified and reused. If it is missing,
`--apply` runs `copy <image_source> flash:<image>` and verifies the downloaded
checksum. Without `--apply`, it only records the planned copy. An existing file
with a bad checksum is flagged and is never silently overwritten. No boot
settings, configuration saves, install commands, conversions or reloads run in
this mode. Images are staged on **active flash**; install distributes software
to the stack members during the later upgrade.

Progress and archives label this operation `stage_image`. Successful apply ends
at `staged`, including when the image was already present. Copy/checksum failure
after a transfer was attempted ends at `staging_failed`; a partial or invalid
file may remain for inspection. The normal upgrade invocation later rechecks
the image and collects a fresh full operational baseline.

## Installation and validation

After checksum and flash checks, it rechecks config/version for concurrent
changes, then executes:

```text
configure terminal
no boot system
boot system flash:packages.conf
no boot manual
end
write memory
install add file flash:<approved-image>.bin activate commit
```

The install command distributes software to stack members. Only the recognized
reload and verified `packages.conf` boot confirmations are answered. Unexpected prompts, explicit installer errors,
failed saves and bad checksums stop the device workflow. Installation is sent
once; an ambiguous disconnect/timeout is checked by reconnecting, never by
resending the install command. Fresh observations must confirm the target release
in install mode, all original members and their committed image state, and the
saved `packages.conf` autoboot configuration.

The defaults allow 30 minutes per install/copy/checksum operation, 30 minutes for
reconnection, an initial 120-second settling period, a 600-second validation
window and 300 seconds for each configuration dump, the slowest read on a large
stack. Two consecutive comparisons without errors are required. Use
`--install-timeout`, `--reload-timeout`, `--settle-seconds`,
`--validation-timeout`, `--poll-interval`, `--show-timeout` and
`--config-timeout` for your tested environment. Deadlines are checked between individual bounded operations; they
are not hard wall-clock process limits.

While it waits, every `converging` event says why: the checks that still differ
from the baseline with their row counts and the affected interfaces or
neighbors, how many clean comparisons are still needed, and how long remains
before validation fails. The final `validation_failed` message carries the same
list, and the NetBox summary's `post_validation.pending` holds it per check.
MAC and IP addresses and configuration lines stay in the local archive.

## Baseline and comparison coverage

Both snapshots contain raw command evidence, parsed tables, counts, errors and
warnings. The same detected routing checks run again even if a router stanza
disappears after the upgrade. Table comparisons are order-independent multisets;
they flag additions and removals, including replacements with equal totals.

| Check | What is compared |
| --- | --- |
| Running/startup config | Ordered configuration diff of everything after the `version` line; the byte-count, `service compress-config`, timestamp and post-reload headers above it and NTP clock-period are omitted. Certificate chains are compared by trustpoint and certificate identity, because startup-config stores certificate bodies as NVRAM file references while running-config prints them in full. Intended boot changes are checked separately against the exact saved settings. |
| NAC | Interface, MAC, authentication method, domain and authorization status; total/status counts and sessions per port. Session IDs are excluded because they regenerate. |
| MAC table | MAC, VLAN, type and destination ports; total and per-port counts. Parsed MAC identities and device-reported totals are cross-checked. |
| Interfaces | Link status, access/trunk VLAN, duplex, speed; IPv4 and, when enabled, IPv6 interface/address state. |
| VLAN/STP | VLAN membership, STP port role/state/cost and available STP root identity/port information. |
| Port channels | Bundle status/protocol and individual member status. |
| Stack/hardware | Member MAC/readiness, exact model/version/mode and inventory PID/serial identities; available redundancy state. |
| Routing | Detected OSPF/OSPFv3, BGP, EIGRP and IS-IS neighbors with stable identity/state. BGP includes received prefix counts and explicit configured VRF summaries. IPv4 routes and discovered VRF IPv4/IPv6 routes include next hops; IPv6 routes are required when IPv6 is configured. |
| Gateway/discovery | Available ARP, HSRP, VRRP, CDP and LLDP tables; HSRP/VRRP become required when configured. |
| PoE | Available per-port administrative/operational power state and powered-device/class information. |
| Resources/health | New identifiable environment alarms, one-minute CPU at or above 90%, processor free memory below 10%, available NTP peer selection/reachability/stratum. Error counters exceeding their baseline are warnings; counter resets are not treated as failures. |

Additional raw diagnostics include boot/install status, stack ports, environment,
CPU/memory details, interface error counters, time, logs, trunks, IPv4/IPv6 protocol
summaries, IPv6 neighbors, detailed access sessions, AAA servers and RADIUS
statistics. Their full text is retained for investigation; not every field in
these diagnostics has an automatic semantic comparison. Authentication traffic,
endpoint reachability and application behavior are not tested end to end.

Critical command/parser failures block apply. Unknown routing protocol stanzas
(for example RIP) also block it until a supported comparator is added. Optional
unsupported diagnostics are explicitly reported as warnings, not empty healthy
tables. Previously available tables disappearing post-upgrade are errors.
Normal endpoint churn can still cause a failed comparison; there are no silent
count tolerances. Use the convergence window and investigate the recorded delta.

## Progress webhook contract

Configure the following in the **same `.env`** as the standards tooling:

```dotenv
NETOPS_UPGRADE_WEBHOOK_URL=https://operations.example.com/api/upgrades/events
NETOPS_UPGRADE_WEBHOOK_TOKEN=your-bearer-token
NETOPS_UPGRADE_WEBHOOK_TIMEOUT=10
```

Both URL and token must be set together. The transport posts JSON with
`Authorization: Bearer <token>`, verifies TLS and rejects redirects. This feed
is separate from `NETOPS_NAC_WEBHOOK_*`. Without upgrade webhook settings,
progress still goes to the terminal and private archive.

Example event (IDs/timestamps abbreviated for readability):

```json
{
  "schema_version": 1,
  "event_type": "upgrade.progress",
  "event_id": "run-uuid:atl-access-01:7",
  "run_id": "run-uuid",
  "device": "atl-access-01",
  "device_id": 123,
  "hostname": "192.0.2.10",
  "site": "atl",
  "role": "access-switch",
  "sequence": 7,
  "timestamp": "2026-09-11T20:00:00.000+00:00",
  "dry_run": true,
  "stage": "precheck_complete",
  "message": "Baseline and upgrade plan captured",
  "summary": {
    "counts": {"nac": 40, "mac": 47},
    "target_version": "17.18.4",
    "starting_versions": ["17.18.1"],
    "bundle_conversion": false
  }
}
```

All devices emit `queued` before workers start. Active stages include
`connecting`, `saving_config`, `precheck`, `precheck_complete`,
`bundle_mode_flagged`, `unsaved_changes`,
`image_verification`, `ready`, `staging`, `configuring_boot`, `installing`,
`reconnecting`, `converging`, `postcheck` and `validating`; BIG-IP units also
emit `backing_up` (UCS archive) and `failing_over`. EOS switches use the IOS XE
stages without `configuring_boot`: `installing` is the `install source` command,
which changes boot-config and reloads.
Terminal stages are `dry_run_complete`, `already_current`, `blocked`, `staged`, `staging_failed`,
`completed`, `completed_with_warnings`, `validation_failed`, `failed` and
`recovery_required`. `summary` is optional and contains counts/version/gate
information at relevant stages.
Events also carry `operation` (`upgrade` or `stage_image`) to distinguish a
pre-staging run from an installation. Long install/copy dialogues emit periodic
progress; show/checksum calls report their current phase while waiting.

The receiver should deduplicate by `event_id`, keep per-device state using
`(run_id, device)` and advance only to a higher `sequence`. Accept with any 2xx
response. Delivery retries reuse the exact event three times with short backoff.
The archive records each event before delivery, with `pending`, `delivered`,
`failed` or `disabled` delivery status. Configurations and raw command output
stay in the archive and are not included in progress events, with one
exception: the final `completed`, `completed_with_warnings` or
`validation_failed` event also carries `report`, with `format` (`text/html`),
`html` (a self-contained page), `markdown` and `path`, so a receiver can store
or display the comparison. It contains the normalized configuration diff and
the changed table rows, redacted like the archive, and can run to a few hundred
kilobytes. Set `NETOPS_UPGRADE_WEBHOOK_REPORT=false` to leave it out. NetBox
never receives it, and it is not stored in the archive's event list.

A failure to deliver the final `ready` event blocks boot and install changes. Once a
device is changing, webhook outages do not interrupt its recovery/validation.
An undelivered event causes a nonzero run exit even if the upgrade succeeds.
There is no background event replay daemon; failed events remain in the report
for reconciliation. Cross-process/distributed UI events may arrive out of order.

## Archives, failures and recovery

Use `--report` or `--report-dir` (or `NETOPS_REPORT_DIR`) as with standards
deployment. Every upgrade that reaches post-validation, whether it completes or
fails validation, also writes a readable comparison beside the archive, named
`<archive stem>_<device>.diff.md`, with the same content rendered as a
self-contained web page in `<archive stem>_<device>.diff.html`, and recorded as
`diff_report` in the device's final event. It lists the findings, software and stack members before and
after, the normalized running-config diff with boot settings shown separately,
every table as an unordered comparison of stable fields with the removed and
added rows, routing neighbors, health, and a line-level comparison of the raw
diagnostics with times, dates, uptimes and readings masked. Reordered tables are
not differences; ages, uptimes, timers and timestamps are excluded; log and
counter output is listed as not compared. It carries the same redaction and
file permissions as the archive and is as sensitive. Each device record includes `pre`, `post`, `upgrade_plan`,
`findings`, `upgrade_events`, install/copy transcripts and `change_attempted`.
Archives are written atomically with private file permissions; known credential
values and common configuration secrets are redacted. Treat the archive as
sensitive device configuration even after redaction. It is not a restorable
full-secret configuration backup.

Whenever running and startup configuration differed when they were read,
`upgrade_plan.saved_config_diff` in the local report holds the normalized
difference from startup to running config, and `upgrade_plan.configuration_saved`
records whether `--apply` had already saved. The diff is not sent in the NetBox
progress message or webhook.

Exit 0 means all device workflows passed without delivery failures; dry-run
pending upgrades are not an error. `completed_with_warnings` also exits 0 when
there are no error findings. Blocked devices, failures, validation errors or
delivery failures exit nonzero. Setup/argument errors use the shared CLI error
handling. A failing device does not stop other independent Nornir workers.

Local advisory locks prevent two upgrade processes in this checkout from
operating on the same management address concurrently. They do not coordinate
separate machines or unrelated configuration tools. Do not run competing
upgrade jobs from multiple controllers. An interrupted worker may leave its
last in-progress event; the UI should mark stale runs for investigation.

`recovery_required` means a change may already have happened. Inspect the saved
transcript and console before taking another action. There is no automatic
image downgrade, destructive package cleanup, multi-hop upgrade, golden-ROMMON
update, or upgrade rollback through the configuration `rollback` command.
One-shot `activate commit` commits before operational validation; validation
failure does not undo the installation. Maintain a separately tested recovery
procedure and console access for the selected model/release path.

## Validation and Cisco references

The offline suite exercises parser transcripts, version/model/stack gates,
dry-run immutability, image checks, install dialogue, failure handling,
post-check deltas and progress delivery. Run:

```bash
python -m pytest tests/test_upgrade.py tests/test_upgrade_eos.py tests/test_upgrade_f5.py \
  tests/test_upgrade_scheduler.py tests/test_webhook.py -q
```

Before applying in production, test your exact PID and release path in a lab,
including stack reloads, bundle conversion when enabled, image transport,
actual NAC/routing outputs and the UI webhook receiver. Confirm target release
notes, licensing/ROMMON prerequisites and recovery behavior. Parser tests alone
do not establish that a particular software migration is vendor-supported.

The C9350 image names and introductory software versions are documented in
Cisco's [C9350 IOS XE 17.18.x release notes](https://www.cisco.com/c/en/us/td/docs/switches/lan/c9000/release-notes/c9350-series-smart-switches-release-notes-1718x.html)
and [C9350 IOS XE 26.1.x release notes](https://www.cisco.com/c/en/us/td/docs/switches/lan/c9000/release-notes/c9350-series-smart-switches-release-notes-261x.html).
Review the caveats for your selected path, including stack/ROMMON upgrade
caveats. The [C9350 stacking guide](https://www.cisco.com/c/en/us/td/docs/switches/lan/c9000/infra/stacking/stacking.html)
describes the stack roles and management commands used by the baseline.

The shared install command and boot preparation are described in Cisco's
[Catalyst 9300 upgrade procedure](https://www.cisco.com/c/en/us/support/docs/switches/catalyst-9300-series-switches/222280-upgrading-catalyst-9300-switches.html).
Consult the [Catalyst 9000 upgrade guide](https://www.cisco.com/c/en/us/support/docs/switches/catalyst-9300-series-switches/216231-upgrade-guide-for-cisco-catalyst-9000-sw.html)
and the [C9300 install-mode configuration documentation](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/17-12/configuration_guide/sys_mgmt/b_1712_sys_mgmt_9300_cg/performing_device_setup_configuration.html)
for install states and conversion details. The C9350 tests use synthetic
transcripts with its PIDs and image names; they are not a live C9350 lab
qualification. A profile records your team's tested path; the script does not
infer approval from release ordering or a shared IOS XE command alone.

## Arista EOS upgrades

The same command, profile shape, NetBox scheduling, progress events, archive
and comparison report drive Arista switches over SSH with netmiko's
`arista_eos` driver; eAPI is not used. A profile is an EOS profile when its
`image` is an `EOS-<release>.swi` or `EOS64-<release>.swi` file. Devices need
the `arista-eos` platform in NetBox (or a blank platform) and the same `NET_*`
credentials with enable access. See `upgrade-profile-eos.yaml.example`.

```yaml
name: leaf-upgrade
models:
  - DCS-7050SX3-48YC8          # exactly as `show version` prints after "Arista"
starting_versions:
  - "4.30.5M"
target_version: "4.32.1F"
image: EOS-4.32.1F.swi
md5: REPLACE_WITH_ARISTA_PUBLISHED_MD5
minimum_free_bytes: 200000000        # headroom on flash beyond the .swi; at least 100000000
image_source: http://images.example.com/EOS-4.32.1F.swi   # optional; switch-reachable HTTP(S)/TFTP
```

Order of operations under `--apply`: `write memory`; baseline; the image
verified on flash, or copied with `copy <image_source> flash:<image>`, and its
`verify /md5` checked against the profile (an existing file with a different
checksum is never overwritten); the `ready` gate; a fresh check that the
configuration and release are unchanged since the baseline and that the running
and startup configurations are still identical; `install source flash:<image>
now reload`; reconnect; then the same convergence loop and comparison report.
The driver never writes `boot system` itself: EOS's install command points
boot-config at the image and reloads, and because the image is already on flash
and checksum-verified it copies nothing. `show boot-config` is checked against
the target after the reload. `--stage-only` copies and verifies the image only.
A dry run reads everything and writes nothing. EOS boots one image file, so
there is no bundle conversion or stack; `bundle_conversion_validated`,
`volume`, `allow_active` and `license_check_date` are rejected in an EOS
profile. The install dialogue answers only `Proceed with reload? [confirm]`; a
save prompt, any other question, a device error or a return to the prompt
without the reload broadcast stops the workflow with `recovery_required` for
inspection.

Prechecks block on: a model outside `models`, a release outside
`starting_versions`, MLAG in any state other than `Active` or `Disabled`, an
active MLAG whose peer link is not up and negotiated or whose peer
configuration is inconsistent, and a unit already running the target whose
boot-config points at a different image. An MLAG peer is a separate device:
upgrade one member, let MLAG return to `Active`, then schedule the other. NetBox
redundancy groups refuse a target set that contains both members.

The baseline compares interface status, IP interfaces, the MAC table, VLANs,
port channels, spanning-tree port roles/states and root identities, MLAG
status, port counts and per-MLAG interface state, VRRP groups, LLDP neighbors,
VRFs, IPv4 routes and ARP per VRF, and the BGP (every VRF, IPv4 and IPv6),
OSPF, OSPFv3 and IS-IS neighbors detected from the configuration, all as
unordered sets of stable fields. Port-channels come from `show port-channel
summary`; a release that rejects it (older EOS has only `brief`, `detailed`,
`limits` and `load-balance`) is read with `show port-channel brief`, which
records each channel's active ports. Either way, an empty answer is accepted
only when the running configuration has no `interface Port-Channel`; an
unrecognized table blocks, and the message quotes how the output begins. The
running configuration is compared with its metadata comments removed. Health takes CPU busy share from `show
processes top once` (100 minus idle; EOS has no one-minute counter), free
memory from `show version`, NTP peers, environment alarms and interface error
counters. Post-checks additionally require the target release, `show
boot-config` pointing at the target image and a saved configuration, and warn
when `show reload cause` is not the requested user reload. IPv6 routes, IPv6
neighbors, trunks and the reload cause are retained as raw diagnostics only;
PoE is not collected. A `router` block other than bgp, ospf, ospfv3, isis or
the non-neighbor blocks EOS uses (general, multicast, bfd, pim, igmp, msdp and
similar) blocks the upgrade until a comparator exists.

This flow has been exercised against synthetic SSH transcripts only and is
written for single-supervisor switches: `install source` also copies the image
and boot-config to a standby supervisor and reloads both, but the prechecks and
reconnect logic do not model a dual-supervisor chassis. Validate your exact
model, release path and MLAG design in a lab before scheduling production
switches; Arista's release notes for the target list the supported
upgrade paths.

## BIG-IP upgrades

The same command, profile shape, NetBox scheduling, progress events, archive
and comparison report drive F5 BIG-IP units over iControl REST. A profile is a
BIG-IP profile when its `image` is a `BIGIP-<version>-<build>.iso`; the image
upload, checksum and free-space checks come from the former
`scripts/f5-image-push` tool. Devices need the `f5-tmos` platform in NetBox
(or a blank platform) and the same `NET_*` credentials with the Administrator
role. The `--f5-port`, `--f5-timeout`, `--f5-verify-tls`/`--f5-insecure` and
`--f5-login-provider` flags and their `NETOPS_F5_*` settings apply.

```yaml
name: bigip-17.5
models:
  - BIG-IP i5800          # marketing names from cm/device, or platform IDs such as C119
  - BIG-IP Virtual Edition
starting_versions:
  - "17.1.1.3"
target_version: "17.5.1.8"
image: BIGIP-17.5.1.8-0.0.19.iso
md5: REPLACE_WITH_F5_PUBLISHED_MD5
minimum_free_bytes: 1500000000       # reserve in /shared/images beyond the ISO
image_source: /var/lib/netops/images/BIGIP-17.5.1.8-0.0.19.iso   # or an https URL the worker downloads
license_check_date: "2025-03-01"     # from F5 K7727 for the target; blocks units whose license predates it
volume: HD1.2                        # optional; default is the inactive volume, or a new one
allow_active: false                  # true: an active HA member fails over to its peer before installing
ucs_backup: true
```

Order of operations under `--apply`: `save sys config`; baseline; image
verified in `/shared/images` or uploaded from `image_source` and md5-checked
on the unit (an existing file with a different checksum is never overwritten);
the `ready` gate; a UCS archive saved on the unit as
`<device>-pre-<target>.ucs` and, with `--ucs-dir` or `NETOPS_UCS_DIR`,
downloaded and checksum-verified (set `NETOPS_F5_UCS_PASSPHRASE` to encrypt
it; UCS files contain private keys); then install to the boot volume, reboot
to it, reconnect, the same convergence loop and the comparison report.
`--stage-only` uploads and verifies the image only. A dry run reads
everything and writes nothing. An `image_source` URL is downloaded to the
worker's image cache first (`--image-cache` or `NETOPS_IMAGE_CACHE`, default
`<project>/.images`) and checksum-verified there before upload.

Prechecks block on: an unapproved platform, a release outside
`starting_versions`, an install already in progress on a volume, a device
group that is not `In Sync`, an active HA member unless `allow_active` is set,
and a license service check date older than `license_check_date`. With
`allow_active`, the unit runs `failover standby` and waits to become standby
before installing, so upgrade the standby first and schedule the active unit
afterwards. Reboots are not coordinated across a pair beyond that check; a
standalone unit is upgraded with the outage that implies.

The baseline compares virtual server, pool and node availability and enabled
state, pool active member counts, interface status, VLANs, self IPs, routes,
trunks, provisioning, iRule names and certificate names and expiry, all as
unordered sets. Post-checks additionally require the target release on the
planned volume and a readable license, and warn when the failover state or
sync status is not what the plan expected, since sync is normally pending
until both members are upgraded. Configuration text is not compared; the UCS
is the configuration record. Hotfix ISOs, vCMP guests, multi-blade chassis,
and `install` to a volume without a reboot are not implemented.

This flow has been exercised against synthetic iControl REST responses only.
Validate your exact platform, release path and HA configuration in a lab
before scheduling production units.

## Schedule through NetBox

[NetBox scheduling](SCHEDULED_UPGRADES.md) adds a native device queue and a
`configure.py upgrade-poll` worker for one-minute cron. It uses these same
profiles, credentials and validation checks, with frozen targets, start windows,
per-device claims, heartbeats and results reported back to NetBox.
