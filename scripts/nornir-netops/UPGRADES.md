# Catalyst IOS XE upgrades

`configure.py upgrade` uses this directory's shared `.env`, AWS Secrets Manager
login resolution, NetBox inventory/filtering, threaded Nornir runner and private
JSON archives. It is read-only unless `--apply` is present. NetBox is the default
inventory for this command, even if standards deployment defaults to CSV.
Explicit `--csv` and `--ip` still work for lab runs.

The driver supports **Cisco C9350 IOS XE** profiles and the existing C9300-family
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
`--limit`, poller ownership/autofilter and NetBox credentials from AWS.
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
without image staging or configuration writes. Unexpected starting releases,
mixed stack versions/modes, unapproved PIDs, incomplete critical output, unsaved
configuration and insufficient flash produce `blocked` with reasons.

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
reconnection, an initial 120-second settling period and a 600-second validation
window. Two consecutive comparisons without errors are required. Use
`--install-timeout`, `--reload-timeout`, `--settle-seconds`,
`--validation-timeout`, `--poll-interval` and `--show-timeout` for your tested
environment. Deadlines are checked between individual bounded operations; they
are not hard wall-clock process limits.

## Baseline and comparison coverage

Both snapshots contain raw command evidence, parsed tables, counts, errors and
warnings. The same detected routing checks run again even if a router stanza
disappears after the upgrade. Table comparisons are order-independent multisets;
they flag additions and removals, including replacements with equal totals.

| Check | What is compared |
| --- | --- |
| Running/startup config | Ordered configuration diff; volatile headers, IOS `version` and NTP clock-period omitted. Intended boot changes are checked separately against the exact saved settings. |
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
`connecting`, `precheck`, `precheck_complete`, `bundle_mode_flagged`,
`image_verification`, `ready`, `staging`, `configuring_boot`, `installing`,
`reconnecting`, `converging`, `postcheck` and `validating`.
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
stay in the archive; they are not included in the webhook event.

A failure to deliver the final `ready` event blocks new device writes. Once a
device is changing, webhook outages do not interrupt its recovery/validation.
An undelivered event causes a nonzero run exit even if the upgrade succeeds.
There is no background event replay daemon; failed events remain in the report
for reconciliation. Cross-process/distributed UI events may arrive out of order.

## Archives, failures and recovery

Use `--report` or `--report-dir` (or `NETOPS_REPORT_DIR`) as with standards
deployment. Each device record includes `pre`, `post`, `upgrade_plan`,
`findings`, `upgrade_events`, install/copy transcripts and `change_attempted`.
Archives are written atomically with private file permissions; known credential
values and common configuration secrets are redacted. Treat the archive as
sensitive device configuration even after redaction. It is not a restorable
full-secret configuration backup.

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
python -m pytest tests/test_upgrade.py tests/test_webhook.py -q
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

## Schedule through NetBox

[NetBox scheduling](SCHEDULED_UPGRADES.md) adds a native device queue and a
`configure.py upgrade-poll` worker for one-minute cron. It uses these same
profiles, credentials and validation checks, with frozen targets, start windows,
per-device claims, heartbeats and results reported back to NetBox.
