# Upgrade engine handover

For the next person or agent who picks this up. It explains how the upgrade
engine is put together, the invariants that keep it safe, how the NetBox queue
drives it, how it is tested, and what a new platform has to provide. Arista
EOS is the next platform; section 7 is written around it.

Operator documentation is elsewhere: [UPGRADES.md](UPGRADES.md) for running
IOS XE and BIG-IP upgrades, [SCHEDULED_UPGRADES.md](SCHEDULED_UPGRADES.md) for
the NetBox queue, [RUN_ARCHIVES.md](RUN_ARCHIVES.md) for the archive format.

## 1. Shape of the system

Two halves that never share a process:

- **The worker** lives in `scripts/nornir-netops/netops/upgrade/`. It connects
  to devices, decides, changes, validates and reports. It runs either by hand
  (`configure.py upgrade`) or from cron (`configure.py upgrade-poll`).
- **The queue** lives in the `netbox-discovery` plugin
  (`plugins/netbox-discovery/netbox_discovery/upgrade_*.py` and
  `api/upgrades.py`). It holds schedules, hands out claims, records progress
  and enforces ordering. Nothing in NetBox ever connects to a device.

| File | Responsibility |
| --- | --- |
| `netops/upgrade/profile.py` | The approved path: models, starting versions, target, image, checksum. Decides the **family** from the image name and validates per family. No implicit version selection anywhere. |
| `netops/upgrade/checks.py` | IOS XE baseline: which commands are read, how they are parsed (ntc-templates plus identity cross-checks), the generic `compare()` and the message builders. |
| `netops/upgrade/workflow.py` | IOS XE device workflow: `preflight`, `Device` (netmiko wrapper), image verification, boot preparation, the install dialogue, `wait_for_target`, `settle`, `converge`. Also the family dispatch at the top of `upgrade_device`. |
| `netops/upgrade/f5.py`, `f5_workflow.py` | The same stages for BIG-IP over iControl REST. The template to copy for a new platform. |
| `netops/upgrade/progress.py` | `Reporter`: one durable event stream per device, archived before delivery, optional bearer webhook, optional NetBox sink. |
| `netops/upgrade/report.py` | The before/after comparison written beside the archive as Markdown and a self-contained HTML page. |
| `netops/upgrade/cli.py` | `configure.py upgrade`: arguments, redundancy-group check, the Nornir run. |
| `netops/upgrade/scheduler.py` | `configure.py upgrade-poll`: one cron tick; outbox replay, claim, per-job archive, heartbeat, run. |
| `netops/archive.py`, `netops/webhook.py`, `netops/netbox.py`, `netops/inventory.py`, `netops/credentials.py` | Shared with the standards tooling: archives and redaction, webhook transport, NetBox client and inventory, CSV inventory, credential resolution. |
| plugin `upgrade_models.py` | `UpgradeJob` (one device, frozen at scheduling), `UpgradeGroup`, `UpgradeDependency`. |
| plugin `upgrade_queue.py` | Every state change: schedule, claim, report, cancel, hold, release, expiry. All under row locks. |
| plugin `upgrade_groups.py` | Group membership, dependency closure, wave planning, discovery from FHRP groups and cables. |
| plugin `api/upgrades.py` | `schedule/`, `check-in/`, `{id}/report/`, `{id}/cancel/`, hold and release endpoints. |
| plugin `upgrade_views.py` | Schedule form with preview, job list and page, edit of pending jobs, group and dependency CRUD. |

## 2. One device, start to finish (IOS XE)

`workflow.upgrade_device(task, profile, options, reporter)` is one Nornir
task per management address. A stack is one device: its active member is the
management address and every member is verified.

| Stage | What happens | Device writes |
| --- | --- | --- |
| `queued` | Emitted for every target before workers start. | none |
| `connecting` | SSH via netmiko, `enable`. The platform must be `cisco_ios` or blank; blank is set to `cisco_ios` for the read-only discovery and the observed PIDs still have to pass the profile. | none |
| `saving_config` | `--apply` (upgrade operation only): `write memory` before anything is read, so the running/startup comparison becomes "did the save take". This is the one write before the `ready` gate. | `write memory` |
| `precheck` | `checks.collect` (or `collect_staging` for `--stage-only`): one event per command. | none |
| `precheck_complete` | `preflight` builds the plan: blockers, whether a BUNDLE to INSTALL conversion is needed, already-current, unsaved changes. The event carries `pre` and `upgrade_plan` into the archive and a small `progress_summary` for the UI. | none |
| `bundle_mode_flagged`, `unsaved_changes` | Informational. | none |
| `already_current` | Terminal when every member is on the target in INSTALL mode with healthy boot settings. | none |
| image check | `verify_image` in read-only mode: `dir flash:<image>`, `dir flash-N:` for every member's free space. A failure is a blocker, not an exception. | none |
| `blocked` | Terminal with the joined blocker list. Nothing was changed. | none |
| `dry_run_complete` | Terminal without `--apply`. | none |
| `ready` | **The gate.** The event must be delivered to the webhook (if configured) and acknowledged by NetBox (if scheduled) or the device is `failed` with no changes. | none |
| `staging`, `image_verification` | Only when the image was missing and `image_source` is set: `copy <url> flash:<image>`, then `verify /md5`. Sets `changed`. | copy |
| `staged` | Terminal for `--stage-only`. | |
| stale check | Running config and `show version` are re-read and compared with the baseline; drift aborts before the first boot write. | none |
| `configuring_boot` | `no boot system`, `boot system flash:packages.conf`, `no boot manual`, `write memory`; both configs re-read; boot lines verified; nothing else may have changed. `changed` is set before the first command. | boot config, `write memory` |
| `installing` | `install add file flash:<image> activate commit` through `Device.interactive`: only the reload confirmation and the packages.conf confirmation are answered; anything else stops the workflow. The command is sent once, never resent. | install |
| `reconnecting` | `wait_for_target`: within `--reload-timeout`, every baseline member must be back on the target release in INSTALL mode and `Ready` in `show switch`. Each event says which members are still missing or not ready. | none |
| `converging` | `settle` sleeps `--settle-seconds`, then the loop. | none |
| `postcheck`, `validating`, `converging` | `converge`: collect a fresh snapshot, `checks.compare(before, after)` plus `target_findings`; two consecutive passes without error findings are required. The validation window starts after the first complete post-check. | none |
| `completed`, `completed_with_warnings`, `validation_failed` | Terminal. The comparison report is written and attached to the final webhook event. | none |

Exceptions map to a terminal status by the `changed` flag: `failed` when
nothing was written, `recovery_required` when something may have been,
`staging_failed` for a stage-only copy that went wrong. The message is the
redacted exception text for `ValueError` and `TimeoutError`, otherwise only
the exception type, because transport errors can embed credentials.

`Result(failed=True)` is returned for blocked, failed, validation-failed and
recovery states; the CLI exit code is nonzero for any failed device or any
undelivered event.

## 3. Data contracts

**Profile** (`profile.Profile`): frozen dataclass loaded from YAML or from the
NetBox job's JSON. `family` is derived from the image name: `.iso` is `f5`,
otherwise the first PID decides `C9350` or `C9300`. `_validate_ios_xe` and
`_validate_f5` hold the family rules. Introductory releases per family and
per PID are the `FAMILY_FLOORS` and `PID_FLOORS` tables.

**Snapshot** (what `collect` returns and what everything downstream reads):

| Key | Shape | Consumers |
| --- | --- | --- |
| `raw` | `{command: output}` | archive, `report.diagnostics_section`, `preflight` (`show install summary`) |
| `software` | `{member: {model, version, mode}}` | `preflight`, `wait_for_target`, `target_findings`, `report.software_section` |
| `stack` | `{member: {mac, state}}` | `preflight`, `wait_for_target`, `compare` (identity and readiness) |
| `config`, `startup_config` | normalized text | `preflight`, `boot_findings`, `compare`, `report.config_section` |
| `tables` | `{name: [row, ...]}` with stable fields only | `compare` (unordered multiset), `metrics`, report |
| `routing` | `{check: [peer, ...]}` | `compare`, report |
| `routing_commands` | `{check: (command, kind)}` | passed back into the post-check so a vanished protocol is still checked |
| `errors`, `warnings` | `{key: message}` | `preflight` (errors are blockers), `compare` (post-check errors are findings) |
| `metrics` | `{counts: {...}, ...}` | `progress_summary` in events; `counts` is required |
| `health` | `alarms`, `interface_errors`, optional `cpu_percent`, `memory_free_percent`, `ntp` | `compare`, `report.health_section` |
| `facts` | platform-specific facts (BIG-IP only today) | that platform's `preflight` and `target_findings` |

`checks.compare` is platform-neutral: it only needs `errors`, `warnings`,
`tables`, `routing`, `stack`, `config`, `startup_config` and `health`. The
BIG-IP collector produces exactly this shape with one synthetic member `"1"`
and `mode` set to the active boot volume; that is the minimum a new platform
must produce. Rows are compared by `checks.canonical`, which drops the
`VOLATILE_FIELDS` (ages, uptimes, timers) and sorts list values.

**Plan** (`upgrade_plan` in the archive): `blockers`, `bundle_conversion`,
`upgrade_needed`, `already_current`, `starting_versions`, `target_version`,
`profile`, `approved_profile`, `operation`, `commands`, `unsaved_changes`,
`saved_config_diff`, `configuration_saved`, `config_mismatch_overridden`,
`image_verification` (`verified` or `pending_transfer`), `image_size_bytes`,
`flash`, `staging_command`.

**Findings**: `{"check", "severity"}` plus one of `message`,
`before_count`/`after_count`/`removed`/`added`, `diff`, or `before`/`after`.
`checks.describe_finding` knows all four shapes; keep to them so progress
messages and the report stay readable. Severity `error` blocks; `warning`
only changes `completed` to `completed_with_warnings`.

**Events**: `Reporter.emit(host, stage, message, payload, changed, attachment)`
assigns the per-device sequence, records `payload` into the archive under the
device, prints, posts to the webhook, then hands the event to the NetBox sink.
`payload["progress_summary"]` is the only payload data that leaves the archive
(as `summary`); `attachment` goes to the webhook only. Terminal stages are
listed in UPGRADES.md; the NetBox outcome map is in `upgrade_queue.report`.

## 4. Invariants

These are the properties the tests pin down. Keep them when changing anything.

1. **Read-only by default.** Without `--apply` no command that changes a
   device is sent, including `write memory`. Tests: `test_dry_run_never_mutates`,
   `test_stage_only_*`.
2. **Every gate re-reads.** An earlier dry run is never permission; the apply
   run collects its own baseline, and re-reads config and version immediately
   before the first boot write.
3. **`ready` is synchronous and never deferred.** If the webhook or NetBox
   does not acknowledge it, nothing is written. The scheduler's outbox refuses
   to persist or replay a `ready`.
4. **Install is sent once.** A timeout or lost session is resolved by
   reconnecting and observing, never by resending. `Device.interactive`
   answers only the recognized prompts.
5. **`changed` decides the failure status.** Set it before the first write of
   each phase, never after. `recovery_required` means a person must look.
6. **Nothing is inferred from release ordering.** A profile lists exact PIDs
   and exact starting versions; the code adds only the vendor's introductory
   floors so impossible profiles are rejected early.
7. **Fail closed on parsers.** An unrecognized table, a row the parser
   dropped, a missing count line: all errors, never an empty healthy table.
   Optional diagnostics that fail are warnings, and are named as such.
8. **Secrets stay out of events.** Config text, MAC and IP addresses and raw
   command output go to the archive only; `archive.clean` redacts known
   secret keys and protected values. Exceptions of unknown type are reported
   by class name only.
9. **One device, one lock.** `device_lock` (per management address, per
   checkout) and NetBox's one-active-job-per-device constraint. Neither
   coordinates with unrelated tools.
10. **Redundancy is checked or refused.** Manual `--apply` runs refuse both a
    group over its limit and an unreadable group list unless `--ignore-groups`
    is passed. The queue enforces groups, dependencies and holds itself.

## 5. The NetBox queue

Statuses: `pending`, `held`, `claimed` (prechecks), `running` (after `ready`),
`completed`, `completed_with_warnings`, `failed`, `recovery_required`,
`expired`, `cancelled`. `ACTIVE` is claimed, running and recovery_required:
the device stays fenced until a person records recovery. `TERMINAL` is the
rest except pending and held.

`claim(user, poller, limit, apply)` runs on every `check-in/`: expire what is
past its window or lost its heartbeat, then walk due pending jobs in
scheduled order under `select_for_update(skip_locked=True)`. A job is skipped
(not failed) while its groups are at capacity, a device it waits for is still
active in any batch, or its batch dependencies are not done; it is `held` when
a partner or dependency has failed or needs recovery (and, by default, when
anything at the same site in the batch has). `check_target` re-verifies the
device name, primary IP, poller ownership, tenant and virtual-chassis master
at claim and again at `ready`.

`report(user, pk, data)` is the only progress entry point: claim token must
match; a terminal job acknowledges retries of old sequences and rejects new
ones; heartbeats only touch `last_seen_at`; sequences are monotonic;
`ready` turns `claimed` into `running` only inside the window; the outcome
map turns terminal stages into statuses, promoting `failed` to
`recovery_required` once `started_at` is set; `hold_related` runs on failure.

The worker side (`scheduler.py`): `tick_lock` serializes cron ticks;
`Outbox.replay` delivers spooled events before any claim and stops the tick
if one cannot be delivered; `validate_assignment` re-validates the profile
with the same validator as the CLI; `platform_for` maps the profile to the
Nornir platform (`f5_tmsh` for `.iso`, `cisco_ios` otherwise); `Heartbeat`
posts every 30 seconds; each job has its own `archive.Run`.

Ordering data: `UpgradeGroup` (max concurrent, `depends_on` groups) and
`UpgradeDependency` (upstream waits for downstream). `refresh_discovered`
derives groups from FHRP assignments and dependencies from cables across role
tiers (`upgrade_tier_roles` setting), updates by `key`, marks vanished ones
stale, never touches manual entries. `plan_waves` gives the preview and the
`planned_wave` on each job; the queue enforces at claim time, not the wave
number.

## 6. Testing

Worker suites run with plain pytest in a venv with `requirements.txt` plus
`pytest` and `requests`:

```bash
cd scripts/nornir-netops
python -m pytest tests/test_upgrade.py tests/test_upgrade_scheduler.py tests/test_webhook.py tests/test_upgrade_f5.py -q
```

Patterns to follow:

- **Transcripts, not devices.** `transcript()` in `tests/test_upgrade.py` is a
  minimal set of IOS XE outputs; `baseline()` runs the real collector over
  it. The C9350 transcript is a string substitution of the C9300 one; nothing
  in the suite is a hardware capture. When a real capture exists, add it as a
  fixture rather than editing the synthetic one.
- **Fake device, real workflow.** The `fake_device` fixture replaces
  `workflow.Device` with a class that serves transcripts and records every
  mutating command in `mutations`. Tests assert on `mutations`, on the stage
  sequence from `stages(reporter)` and on `plan_from(reporter)`. Failure
  injection is a method override on the fake.
- **Real `Device` methods with a mocked connection** for the dialogue and
  reconnect logic (`Device(None, options, Mock())`, then replace
  `connect`, `read`, `connection`).
- **Scheduler tests** use a job dict fixture and the real `Outbox` on
  `tmp_path`; `QueueClient.post` is mocked.

The plugin has no local harness. Its Django tests
(`netbox_discovery.tests.test_upgrades`, `test_upgrade_groups`,
`test_upgrade_poller`) run in a throwaway Docker NetBox matching
`VERSIONS.md` with the plugin bind-mounted and installed into the venv:

```bash
manage.py test netbox_discovery.tests.test_upgrades netbox_discovery.tests.test_upgrade_groups netbox_discovery.tests.test_upgrade_poller --noinput -v 2
```

Read the `Ran N tests` / `OK` lines; the run ends with a "database is being
accessed by other users" teardown error and exit 1 even when green, from the
concurrency test's extra connections.

## 7. Adding a platform: Arista EOS

The engine is family-dispatched, not platform-abstracted: each family has its
own collector and workflow module and shares the profile shape, the `ready`
gate, the reporter, `converge`, `compare` and the report. Follow the BIG-IP
split (`f5.py` for reads and device operations, `f5_workflow.py` for the
stages). Everything below is the checklist with the EOS specifics filled in.
Items marked *confirm* need a lab unit; do not guess them into the code.

### 7.1 Profile (`profile.py`)

- `family`: an image ending in `.swi` is `eos`. Keep `.iso` for `f5` and the
  PID rule for IOS XE.
- `eos_version(value)`: EOS releases look like `4.32.3M`, `4.31.2F`,
  `4.30.1.1F` (four numeric parts plus F for feature or M for maintenance).
  Return a comparable tuple of the numeric parts; keep the letter for display
  and equality, not ordering.
- `_validate_eos`: models are EOS model names as `show version` prints them
  (`DCS-7050SX3-48YC8`, `DCS-7280SR3-48YC8`, `CCS-720XP-48ZC2`); image must be
  `EOS-<version>.swi` or `EOS64-<version>.swi` (*confirm which your fleet
  runs; 64-bit images are platform dependent*) with the release equal to
  `target_version`; starting versions older than the target; `image_source`
  rules as for IOS XE (switch-reachable HTTP(S) URL, no credentials, basename
  equals image). Introductory floors: a per-model-family table like
  `FAMILY_FLOORS`, filled from Arista's release notes for the models you list.
- `Profile.release()` dispatches on family; add the `eos` branch.
- The BIG-IP-only fields must be rejected for EOS the way `_validate_ios_xe`
  rejects them. If EOS needs new fields (for example `reload_mode`), add them
  to the dataclass **and** to `validate_profile` in the plugin's
  `upgrade_queue.py`, or the schedule form will reject the profile.

### 7.2 Reads (`eos.py`)

EOS answers every `show` command as JSON with `| json`; prefer that over
ntc-templates, it is exact and needs no identity cross-checks. Build a
`collect(read, progress)` that returns the snapshot shape from section 3:

- `software`: `{"1": {"model": modelName, "version": version, "mode": <boot image name from show boot-config>}}`
  from `show version | json`; `stack`: `{"1": {"mac": systemMacAddress, "state": "ready"}}`.
  EOS has no stacks; an MLAG pair is two devices and a redundancy group.
- `config` / `startup_config`: `show running-config` and `show startup-config`,
  through an EOS normalizer. EOS prints `! Command: show running-config`,
  `! device: <name> (<model>, EOS-<version>)`, `! boot system flash:/...` and a
  `! Startup-config last modified` line above the body; strip those the way
  `checks.normalized_config` strips the IOS header, and keep `boot system`
  handling separate so the intended boot change is checked on its own.
- `tables` (stable fields only): `show interfaces status | json`,
  `show ip interface brief | json`, `show vlan | json`,
  `show mac address-table | json`, `show ip arp | json`,
  `show port-channel summary | json`, `show spanning-tree | json`,
  `show lldp neighbors detail | json`, `show mlag | json` and
  `show mlag interfaces | json` (required when MLAG is configured),
  `show vrrp | json` when configured, `show inventory | json`. NAC: EOS
  reports 802.1X sessions under `show dot1x hosts` (*confirm the JSON shape
  on your release*).
- `routing`: detect `router bgp`, `router ospf`, `router isis` in the config
  like `checks.routing_checks`, then `show ip bgp summary | json` (per VRF:
  `show ip bgp summary vrf <name> | json`), `show ip ospf neighbor | json`,
  `show isis neighbors | json`; peers keyed by neighbor and state.
- `health`: `show environment temperature | json`, `show environment power | json`,
  `show environment cooling | json` for alarms; `show processes top once` for
  CPU; `memTotal`/`memFree` from `show version | json`; `show ntp status` and
  `show ntp associations` for peers; `show interfaces counters errors | json`.
- `raw`: keep every command output. `report.diagnostics_section` iterates the
  IOS `checks.DIAGNOSTICS` list today, so EOS diagnostics would not appear in
  the report; make that section iterate the raw keys present on both sides
  (minus table commands) as part of this work rather than adding a second
  list.
- `metrics`: at least `counts` per table.

### 7.3 Device operations and workflow (`eos_workflow.py`)

Reuse `workflow.Device` for the netmiko session: `connect`, `read`, `write`
and the lock are platform-neutral. Write EOS-specific pieces beside it:

- **Preflight**: model in `profile.models`; version in `starting_versions` or
  already the target; `show boot-config` names the running image; MLAG state
  `active`, `negStatus` `connected` and `configSanity` `consistent` when MLAG
  is configured; running and startup configs equal after the save; enough
  free space from `dir flash:` (`bytes free` is printed the same way as IOS).
- **Image**: `dir flash:<image>`, `copy <image_source> flash:` when missing and
  `--apply`, then `verify /md5 flash:<image>` against the profile checksum
  (Arista publishes MD5 and SHA512; *confirm `verify /md5` is available on
  your release, otherwise use `verify /sha512` and store that in the profile*).
- **Boot preparation**: `boot system flash:<image>` then `write memory`,
  re-read `show boot-config` and both configs, verify only the boot line
  changed. Set `changed` before the first command.
- **Reload**: `reload now` skips the confirmation prompt; if you use plain
  `reload`, answer only `Proceed with reload? [confirm]` and nothing else.
  Send it once through `Device.interactive`-style handling and treat a lost
  session as the expected outcome. Arista's Smart System Upgrade and MLAG
  ISSU exist; leave them out of the first version and say so in the docs,
  as UPGRADES.md does for ISSU and xFSU.
- **Reconnect**: `show version | json` must report the target release and
  `show boot-config` the new image; on an MLAG pair also wait for
  `show mlag | json` to return to `active`/`connected` before `settle`.
- **Post-check findings** (`target_findings`): target version, boot image,
  MLAG state, saved config equal to running.
- **Dispatch**: add `elif profile.family == "eos"` at the top of
  `workflow.upgrade_device`; require `host.platform in {None, "", "arista_eos"}`
  and set it to `arista_eos`.
- **Recovery statement**: put the honest rollback story in the
  `rollback_unsupported` note of `precheck_complete`. For EOS that is "set
  `boot system` back to the previous image and reload"; the engine does not do
  it automatically.

Stage names must stay the ones in UPGRADES.md; the NetBox outcome map and
the UI only know those.

### 7.4 Scheduler and CLI

- `scheduler.platform_for`: `.swi` returns `arista_eos`.
- `cli.run` banner: the family label.
- NetBox platform slug `arista-eos` already maps to `arista_eos` in
  `netops/netbox.py`; devices need that platform or a blank one, a primary IP
  and poller ownership like any other target.
- `commands.yaml` already has an `arista_eos` section for `configure.py collect`;
  keep the upgrade collector's command list independent of it.

### 7.5 NetBox side

- Profiles pass through the plugin as JSON; `validate_profile` only checks
  the key set and basic types, so nothing changes unless you add profile
  fields.
- Redundancy: MLAG pairs are not something NetBox discovers today. Enter
  them as manual `UpgradeGroup`s (limit 1) or extend
  `upgrade_groups.refresh_discovered` with an `mlag` source derived from
  peer-link cables between devices of the same role. A new source value is a
  new `UpgradeGroupSourceChoices` entry and therefore a migration.
- Dependencies from cables already apply to EOS devices if their roles are
  in `upgrade_tier_roles`.

### 7.6 Tests and docs

- `tests/test_upgrade_eos.py` mirroring `test_upgrade_f5.py`: profile
  acceptance and rejection, collector over captured `| json` outputs, a fake
  device that records mutations, dry run never mutates, apply happy path,
  bad checksum blocks, reload sent once, reconnect requires the boot image
  and MLAG state, post-check delta fails. Capture the JSON from a lab switch
  before and after a real upgrade and keep it as fixtures.
- UPGRADES.md: an "EOS upgrades" section in the shape of "BIG-IP upgrades",
  an `upgrade-profile-eos.yaml.example`, and the README feature table row.
- Add the EOS floors and any new profile fields to section 3 of this file.

### 7.7 Checklist

1. `profile.py`: family, version parser, validator, floors.
2. `eos.py`: collector producing the section 3 shape; normalizer.
3. `eos_workflow.py`: preflight, image, boot, reload, reconnect, findings;
   dispatch in `workflow.upgrade_device`.
4. `scheduler.platform_for`, `cli.run` banner.
5. `report.diagnostics_section` made family-neutral.
6. Plugin: profile fields if any; MLAG groups by hand or discovered.
7. Tests with captured fixtures; docs and example profile.
8. Lab run of the exact model and release path, dry run then `--stage-only`
   then `--apply`, before any NetBox schedule.

## 8. Open items

- **Cisco CSCwt90927.** Upgrading a C9350 stack from 17.18.1 to 17.18.3 can
  corrupt the primary ROMMON during the bootloader step. The engine has no
  per-path deny list and does not parse the bootloader version. Pending a
  decision on whether to add a `blocked_paths` profile field and a ROMMON
  before/after line in the report.
- **Boot line rendering on a real stack.** After `boot system
  flash:packages.conf`, `boot_findings` accepts only that spelling and
  `switch all flash:packages.conf`. If a stack renders one line per switch
  (`boot system switch 1 flash:packages.conf`), the workflow stops after the
  boot write and reports `recovery_required` with "install not started". A
  capture of `show running-config | include ^boot`, `show startup-config |
  include ^boot` and `show boot` from a two-member lab stack after those
  commands settles it; the per-switch form can then be accepted when every
  member has one.
- **Endpoint tables are strict.** NAC, MAC and ARP rows must match the
  baseline exactly on two consecutive passes. Upgrades run off hours by
  policy, so no tolerance was added; if a window is ever busy, expect
  `validation_failed` and read the report's removed and added rows.
- **Scheduled events carry no site or role.** `Reporter` reads them from
  `host.data`, which the scheduler does not populate. NetBox has them on the
  job; the webhook feed does not.
- **`report.diagnostics_section` is IOS-specific** (see 7.2).
