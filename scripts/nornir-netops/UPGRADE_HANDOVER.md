# Upgrade engine handover

For maintainers of the upgrade engine. It explains how the upgrade
engine is put together, the invariants that keep it safe, how the NetBox queue
drives it, how it is tested, and what a new platform has to provide. IOS XE,
Arista EOS and BIG-IP drivers are implemented. Section 7 maps the EOS driver
and the integration points to follow when adding another platform.

Operator documentation is elsewhere: [UPGRADES.md](UPGRADES.md) for running
IOS XE, EOS and BIG-IP upgrades, [SCHEDULED_UPGRADES.md](SCHEDULED_UPGRADES.md) for
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
| `installing` | `install add file flash:<image> activate commit` through `Device.interactive`: only recognized reload, packages.conf and save-configuration prompts are answered; anything else stops the workflow. The command is sent once, never resent. | install |
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
`.swi` is `eos`; otherwise the first PID decides `C3650`, `C3850`, `C9300` or
`C9350`. `_validate_ios_xe`, `_validate_eos` and `_validate_f5` hold the family rules. Introductory releases per family and
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
| `facts` | platform-specific facts | that platform's `preflight` and `target_findings` |

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

Require both passing test results and a successful process exit. If database
teardown fails because concurrent test connections remain open, investigate
the connection cleanup; do not report that run as fully passing.

## 7. Platform integration: Arista EOS

EOS is implemented in `netops/upgrade/eos.py` and `eos_workflow.py`, with
coverage in `tests/test_upgrade_eos.py`. See [UPGRADES.md](UPGRADES.md#arista-eos-upgrades)
for supported operations, profile examples and limitations.

Use the existing drivers as the reference when adding another family:

1. Add image-family detection, release parsing and validation in `profile.py`.
   Keep exact model and starting-version approval in the profile.
2. Provide a collector with the snapshot contract in section 3, preserving
   raw observations and reporting incomplete required reads as errors.
3. Implement preflight, verified staging, boot preparation, a single reload,
   reconnection and target findings in a dedicated workflow. Share the lock,
   reporting, acknowledgement gate and convergence logic.
4. Add dispatch in `workflow.upgrade_device`, platform selection in the
   scheduler, and the CLI family label.
5. Supply `diagnostic_commands` in snapshots so the shared report renders
   the platform's diagnostics. The report falls back to IOS commands only
   when this field is absent.
6. Keep profile fields aligned with the NetBox validator. Configure redundancy
   groups for HA peers and retain the queue's group/dependency checks.
7. Cover read-only behavior, checksums, interrupted writes, reconnection and
   post-check failures with captured fixtures and simulated workflows.
8. Validate the exact hardware and release path in a lab before scheduling
   an upgrade. Parser and workflow tests do not approve a production path.

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
