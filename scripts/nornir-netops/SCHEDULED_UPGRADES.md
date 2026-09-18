# Schedule IOS XE work from NetBox

NetBox holds the schedule; remote workers pull it over outbound HTTPS. Nothing in NetBox connects to a switch. The worker lives alongside `configure.py`, shares its `.env`, AWS Secrets Manager login, and upgrade engine, and exits after one bounded batch.

## In NetBox

Deploy the updated **netbox-discovery** plugin and run its migrations before enabling the worker. No separate NetBox background scheduler is needed: due times are evaluated at check-in.

Open **Discovery → Software → Upgrade Jobs → Add** on the list page (the sidebar **+** also works). Choose a site and role, optionally a platform or explicit devices. Paste the same validated YAML profile used by `configure.py upgrade --profile`; specify the scheduled start and **start before**, including a UTC offset. Preview shows the matching devices and pollers. Scheduling captures those devices, management addresses, poller assignments, and a separate copy of the profile. Later inventory changes cannot add devices to a batch.

Open a pending job and click **Edit** to change its operation, profile, scheduled window or description. This updates only that device's job; its device and poller stay fixed, and other jobs in the batch are unchanged. Editing requires `change` permission, plus `apply` permission when the existing or new operation stages an image or installs an upgrade. The worker receives the updated profile when it claims the job. Claimed, running and closed jobs cannot be edited. A stale browser form is rejected if another edit or worker claim happened in the meantime.

Operations:

| Operation | What the remote does | Worker needs `--apply` |
| --- | --- | --- |
| Pre-upgrade audit | Full baseline, version/mode/path checks and image readiness; no device writes | No |
| Stage image only | Check flash; download a missing image and verify its checksum; no install/reload | Yes |
| Install upgrade | Saves the running configuration, full prechecks, image staging if needed, install-mode upgrade, reload, postchecks | Yes |

A profile whose `image` is a BIG-IP `.iso` schedules BIG-IP units the same way, and one whose `image` is an `EOS-<release>.swi` schedules Arista EOS switches; see [BIG-IP upgrades](UPGRADES.md#big-ip-upgrades) and [Arista EOS upgrades](UPGRADES.md#arista-eos-upgrades). The YAML must represent a path you have validated. The remote runs the full model/version/image validator again; neither an API payload nor an inventory platform bypasses it. See [UPGRADES.md](UPGRADES.md) for supported hardware, checks, and bundle conversion behavior. Do not use the example checksum as a real checksum.

Select only the virtual chassis **master** for a stack. Device `poller-*` tags take precedence over site tags and then the nearest tagged ancestor region. When more than one poller tag applies, choose a matching poller explicitly. Unlike an inventory sweep, a scheduled upgrade is assigned to exactly one poller; it does not use prefix-based inventory unions or a default-region fallback. Missing ownership, ambiguous ownership and tenant mismatches block scheduling. The worker checks the saved ownership and management address again at dispatch and before authorizing changes.

**Start before is a latest start for device changes, not a forced stop or guaranteed completion time.** NetBox checks it again after prechecks, before copying an image or changing boot configuration. The `write memory` that an install job runs at the start of its prechecks is the one device write that precedes that check. A transfer, install, reload or recovery already underway continues past it. Prestage images ahead of the upgrade window when download time is significant. Slots become available on the next cron tick after a batch finishes; allow time for earlier batches and prechecks.

## Redundancy groups, dependencies and holds

Two switches in an HSRP pair, the A and B closets on one floor, or the F5
units behind one load balancer must not be upgraded at the same time, and a
core should not go until the closets it serves are done. **Discovery →
Software → Redundancy Groups** and **Upgrade Dependencies** hold that
knowledge, and the queue enforces it:

- A **group** is a set of devices of which at most **members upgrading at
  once** (default 1) may hold a claimed, running or recovery-required job,
  counted across every batch and poller; 0 lets every member go together,
  which is the right setting for an office's access switches. A device can be
  in several groups; every one of them must have capacity.
- A group can **wait for** other groups: in a batch, its members are not
  claimed until every member of those groups has completed, and across
  batches the two groups never upgrade at the same time. "Office core waits
  for office access" is one relationship, however many switches are in each.
- A device-level **dependency** does the same for one pair of devices;
  discovery creates these from cables. Chains work transitively, and a cycle
  is refused at scheduling.
- The schedule preview shows the resulting **waves** so the order is visible
  before anything is created; the wave is also shown on each job.
- Groups and dependencies are **discovered** where NetBox already knows:
  FHRP groups (HSRP and VRRP, written by `snmp-inventory`) become groups, and
  cables between devices of different role tiers become dependencies, using
  the `upgrade_tier_roles` plugin setting (`core`, `distribution`, `access` by
  default, top down). The scanner refreshes them at the end of each sweep,
  and **Refresh discovered groups** on the group list does the same on
  demand. Anything created by hand is never changed by a refresh; a
  discovered relationship that disappears is marked stale, not deleted.
  Load-balancer pools and anything else discovery cannot see are entered by
  hand.

When a job ends `failed` or `recovery_required`, the pending jobs of its
partners, of the devices that wait for it, and, with `hold_site_on_failure`
(on by default), of every other device at the same site in that batch move to
**held**, with the reason on the job. A device left in `recovery_required`
also holds new jobs for its partners and dependents in later batches until a
person records recovery, because that device is still fenced. A plain failure
in prechecks does not reach other batches. A held job is released from its
page (or all held jobs in the batch at once) or through the API, always with
a reason that goes into the changelog; it then returns to the schedule and
the normal gates still apply. A pending job can also be held by hand. Held
jobs expire with their start window like pending ones.

Manual `configure.py upgrade` runs against NetBox inventory refuse a target
set that contains more members of a group than its limit unless
`--ignore-groups` is given; they do not wait for dependencies, so use the
queue for anything that needs ordering.

### Setting them up

Start from what discovery already found, then add what it cannot see.

1. **Let discovery make the first pass.** Run an `snmp-inventory` sweep, or
   open **Discovery → Software → Redundancy Groups** and click **Refresh
   discovered groups**. Every HSRP or VRRP group with two or more devices
   appears as a group with a limit of 1 and the source *FHRP group*; every
   cable between two tiers appears under **Upgrade Dependencies** with the
   source *Cabled uplink*. No cabled dependencies usually means the role
   slugs do not match `upgrade_tier_roles`; see [Plugin settings](#plugin-settings).
2. **Add a group by hand** with **Redundancy Groups → Add** for anything that
   is not a gateway pair:

   | Field | What to enter |
   | --- | --- |
   | Name | Unique, and what operators will read on a job: `atl-f5-pair`, `atl-floor3-closets`. |
   | Max concurrent | Shown as **Members upgrading at once** on the group page. `1` for a pair or any set that must go one at a time; a higher number for a pool that can lose that many members; `0` when every member may go together. |
   | Members | The devices. For a stack, the virtual chassis **master**: that is the device a job is created for. |
   | Waits for groups | Groups whose members must finish first. Leave empty for a plain pair. |
   | Description | Why the group exists; shown on the group page. |

3. **Order two sets of devices** by putting each set in a group and setting
   **Waits for groups** on the one that goes last. A group used only for
   ordering gets a limit of `0`, so it adds no one-at-a-time rule of its own.
4. **Order two single devices** with **Upgrade Dependencies → Add**:
   **Upstream** is the device that waits, **Downstream** the one that goes
   first. A device cannot wait for itself, and a pair of devices can have
   only one dependency in each direction.
5. **Check the result before scheduling.** On **Upgrade Jobs → Add**, the
   preview lists each device's groups and its **wave**. Wave 1 starts first;
   a device lands in a later wave because a group was full or something it
   waits for is in the batch. A cycle is reported here by device name and
   nothing is created until it is fixed.

Membership, waits-for and dependencies are copied onto each job when the
batch is scheduled. Adding a device to a group afterwards does not change
jobs that already exist; cancel and reschedule them. The limit is the
exception: it is read from the group by name each time a job is about to
start, so changing **Max concurrent** applies to pending jobs straight
away. Do not rename or delete a group while it has pending jobs: a job whose
group name no longer exists falls back to a limit of 1.

### Worked examples

**HSRP or VRRP pair.** Nothing to enter. After a sweep the pair is a group
named for the protocol, group number and virtual address, for example
`HSRP 10 10.20.30.1`, with a limit of 1. Schedule both switches in one
batch: they land in waves 1 and 2, and the second starts only when the first
has completed.

**A and B closets on one floor, no gateway protocol between them.** Add a
group `atl-floor3-closets`, limit `1`, members `atl-3a-sw01` and
`atl-3b-sw01`.

**F5 pair or any load-balancer pool.** Discovery cannot see these. Add a
group `atl-f5-pair`, limit `1`, with both units. For a pool of four that can
run on three, add one group with all four and limit `1`; use `2` only if the
service is sized to run on two.

**Office access switches, then the office core.** Add `atl-access`, limit
`0`, with every access switch, and `atl-core`, limit `1`, with both cores and
**Waits for groups** set to `atl-access`. One batch holding all of them
upgrades every access switch in wave 1, the first core in wave 2 and the
second core in wave 3. If the cables are in NetBox and the role slugs match
the tiers, the cabled dependencies already give the same order and only the
core pair's own group is needed.

**One device behind another with no cable in NetBox**, such as a firewall
that should follow the switch it hangs off: add a dependency with the
firewall as **Upstream** and the switch as **Downstream**.

### Discovered and manual entries

The **Source** column says where an entry came from: *Manual*, *FHRP group
(HSRP/VRRP)* or *Cabled uplink*. A refresh behaves as follows:

- Manual groups and dependencies are never changed.
- On a discovered group, the **members are reset** to what the FHRP group
  shows on every refresh. The name, limit, waits-for groups and description
  are kept, so raising the limit or adding a waits-for on a discovered group
  is safe; editing its members is not. Put extra members in a manual group.
- An FHRP group seen on fewer than two devices makes no group.
- A cable makes a dependency only when both devices have a role listed in
  `upgrade_tier_roles` and the two roles differ. The device whose role comes
  first in the list is upstream and waits.
- When a manual dependency already exists for the same two devices, the
  cable does not add a second one and the manual one stays manual.
- A discovered entry that NetBox no longer supports is marked **stale**, not
  deleted. **Stale entries are still enforced** until a person deletes them;
  sort either list by its **Stale** column, or add `?stale=true` to the list
  URL, to review them. One that is seen again is un-marked.
- A discovered entry deleted while the FHRP group or cable still exists in
  NetBox **comes back** on the next refresh. To switch off a discovered
  group's rule, set its limit to `0` instead. A cabled dependency follows the
  cable and the two roles: correct whichever of those is wrong in NetBox.

### Plugin settings

Both settings belong to the discovery plugin and go in `PLUGINS_CONFIG` in
`configuration/plugins.py`. The file has no `netbox_discovery` block until
one of them is overridden:

```python
PLUGINS_CONFIG = {
    # ... existing plugins ...
    "netbox_discovery": {
        # Device role slugs, top of the network first.
        "upgrade_tier_roles": ["core", "distribution", "access"],
        # Hold the rest of a site's batch when one device there fails.
        "hold_site_on_failure": True,
    },
}
```

- `upgrade_tier_roles` holds device role **slugs**, not names, from the top
  of the network down. The default is `core`, `distribution`, `access`. If
  the roles are named differently, for example `core-switch`,
  `distribution-switch` and `access-switch`, list those slugs or no cabled
  dependency is ever created. A device whose role is not listed takes no
  part, and an empty list turns cabled dependencies off. The change shows
  after NetBox restarts and the next refresh runs; dependencies from the old
  list that no longer apply are marked stale.
- `hold_site_on_failure` (default `True`) holds the other pending jobs at
  the same site in the same batch when a job ends `failed` or
  `recovery_required`. `False` limits holds to the failed device's partners
  and the devices that wait for it.

NetBox reads its configuration at start-up, so restart its containers after
changing either one. On the scanner side, `sync_fhrp_groups = false` under `[sync]` in the
`snmp-inventory` configuration stops both the FHRP write and the refresh
request at the end of a sweep; the **Refresh discovered groups** button still
works.

## Permissions

Use NetBox Object Permissions on the `netbox_discovery.UpgradeJob` object type:

- Operators: `view`, `add` to schedule audits; also `apply` to schedule staging/upgrades. `change` permits cancelling pending jobs and recording recovery. Apply is a separate permission so audit schedulers cannot authorize reloads.
- Workers: `run` to claim jobs and report/heartbeat. They do not need add, apply, change, or delete permission on upgrade jobs. Give the token write capability; read-only tokens cannot claim work.
- Operators need view access to the selected DCIM devices. Optional filters use NetBox's own device filterset. Add/apply/run/change object constraints are enforced, including for batch creation.

Redundancy groups and dependencies have their own object types, `netbox_discovery.UpgradeGroup` and `netbox_discovery.UpgradeDependency`:

- People who maintain them: `view`, `add`, `change` and `delete` on both. **Refresh discovered groups**, in the UI and through the API, needs `change` on `UpgradeGroup`, and it rewrites discovered dependencies as well.
- Operators who only schedule: `view` on both, so the groups, waves and hold reasons they see link to something. Scheduling itself applies every group and dependency regardless of what the scheduler may view.
- The `snmp-inventory` account: `view` and `change` on `UpgradeGroup` with a write-enabled token, for the refresh at the end of a sweep. The sweep succeeds either way: without `view` the scanner treats the endpoint as absent and skips the refresh with only a debug message, and without `change` it logs a warning that the refresh failed.
- Accounts that run `configure.py upgrade` by hand against NetBox inventory: `view` on `UpgradeGroup`. Without it the group check is skipped with a warning rather than blocking the run.
- Holding and releasing jobs is `change` on `UpgradeJob`, not a group permission.

For each remote account, constrain its `run` permission to its assigned poller, for example `{"poller__name": "checkmk-us"}`. Poller names and claim tokens coordinate work; account permissions provide authorization. The existing SNMP permissions remain separate. Current records are readable through ordinary `view` permission; claim tokens are only returned by the claim endpoint.

## On each remote

Install the updated `scripts/nornir-netops` requirements and use the existing `.env` (or explicitly pass `--env-file`). The same settings apply:

```dotenv
NETOPS_POLLER=checkmk-us
NETBOX_URL=https://netbox.example.com
NETBOX_VERIFY_TLS=true
# NETBOX_TOKEN=... or NETBOX_SECRET=prod/network/netbox
# NET_AWS_SECRET=prod/network/device-login
# NET_AWS_REGION=us-east-1
NETOPS_UPGRADE_QUEUE_STATE_DIR=/var/lib/netops/upgrade-queue
NETOPS_REPORT_DIR=/var/lib/netops/reports
# Optional separate UI feed, sent in addition to NetBox progress:
# NETOPS_UPGRADE_WEBHOOK_URL=https://inventory.example.com/hooks/upgrades
# NETOPS_UPGRADE_WEBHOOK_TOKEN=...
```

Keep the state and report directories writable only by the worker account. The state directory is persistent: it holds the cron lock and undelivered progress containing claim tokens, written with mode 0600. It must survive restarts. Do not put it in a temporary directory. Credentials remain in the shared environment/AWS; none are stored in the schedule.

Run one tick manually to process only scheduled audits:

```sh
cd /opt/nornir-netops
.venv/bin/python configure.py upgrade-poll --poller checkmk-us --workers 3
```

Then install a cron entry under the same account. **Including `--apply` permits the worker to execute staging and upgrade jobs explicitly scheduled in NetBox.** Omitting it leaves those jobs pending and only claims audits.

```cron
* * * * * cd /opt/nornir-netops && .venv/bin/python configure.py upgrade-poll --apply --workers 3 >> /var/log/netops-upgrade-poll.log 2>&1
```

Create the log location with suitable ownership first and rotate it. Set `NETOPS_POLLER` in the shared `.env`, or add `--poller NAME` to the command. The built-in process lock makes overlapping cron ticks exit quietly. At most `--workers` jobs (1–20, default 3) are claimed in a tick and run concurrently through Nornir. Long-running jobs send a heartbeat every 30 seconds. An idle tick makes one check-in, creates no run archive, and does not fetch device credentials or connect over SSH.

`--show-timeout`, `--config-timeout`, `--install-timeout`, `--reload-timeout`, `--settle-seconds`, `--validation-timeout`, and `--poll-interval` also apply, as does `--allow-config-mismatch`, which then covers every job the worker runs. Target/profile flags belong on manually invoked `upgrade`; `upgrade-poll` gets them from NetBox. Use `--report-dir` for per-job archives, not `--report`.

## Progress and interrupted runs

Each device has its own NetBox status, phase, baseline and post-validation counts, timestamps, run ID, and event history. Filter the job list by batch, poller, site, role, status or operation. The detail page shows the most recent 500 events; refresh to see updates. The separate bearer-authenticated webhook remains available for a future dashboard and includes `scheduled_job_id` and `batch_id` for scheduled work. Full baseline/configuration snapshots stay in the private remote archive.

The job list and detail page show two separate timestamps:

- **Upgrade poller last seen** records the server's receipt of an `upgrade-poll` queue check-in (including idle ticks), or an accepted job heartbeat/progress report. It applies to every job assigned to that poller. SNMP-only check-ins do not update it.
- **Job last update** is specific to that job and starts when the worker claims it. Pending jobs show **No job updates yet** even when their poller is checking in normally.

These timestamps refresh when the page reloads. Manual `configure.py upgrade` runs do not check in to the scheduling queue. After installing migration `0008_upgrade_poller_last_seen`, the poller timestamp starts at **Never checked in** until new upgrade-worker contact arrives; older shared SNMP/poller timestamps are not treated as proof that the upgrade worker was running.

The queue claims under database row locks and allows only one active job per NetBox device, including across batches and pollers. This coordinates scheduled workers; it cannot prevent a separate operator or manual script from accessing a switch.

There is **no automatic replay of a claimed upgrade**:

- A worker lost during prechecks is marked failed at the next check-in after five minutes without a heartbeat. No start authorization was issued. Create a new schedule after inspecting the failure.
- A worker lost after NetBox authorizes changes is marked **Recovery required**. Its device remains locked against further scheduled work. An original worker that merely lost NetBox connectivity may still finish and report its outcome.
- Failed post-validation, ambiguous install outcomes, and errors after apply authorization also require recovery. Inspect the switch and its archive, stop any surviving worker, then use **Record recovery and release device**, describing the verified state. This releases the scheduling lock; it sends no rollback command.
- Only pending jobs can be cancelled. Active installs are not interrupted by a UI cancellation.

Before changes, the NetBox ready acknowledgement and any configured webhook must succeed. Afterwards, the upgrade engine continues recovery through a callback outage. Progress is saved locally before delivery and retried on the next cron tick, using the same job/sequence for deduplication. A start authorization is never put in the deferred-delivery spool. A lost claim response is not retried; those claims expire without device changes.

If progress remains undeliverable, the poller reports an error and claims no additional work until delivery succeeds. Permission changes or manually closing a job can make old reports permanently unreportable: inspect the corresponding archive and NetBox state before moving those outbox files aside. Never replay a start command manually from the spool. If the entire remote is down, the detail page warns about a stale heartbeat and allows verified recovery of a job that had started; stored stale status transitions occur when its worker next checks in. The API also exposes `heartbeat_stale` and `needs_recovery` without waiting for another check-in.

## API for another scheduling interface

All paths below are under `/api/plugins/discovery/upgrade-jobs/` and use the normal NetBox API token. The optional UI webhook has its own bearer token and URL; it is not used for queue authentication.

- `POST schedule/`: `{filters, profile, operation, scheduled_at, start_before, poller?, description?, preview?}`. `filters` accepts the same fields as `/api/dcim/devices/`, such as `{"site": ["atl"], "role": ["access"]}`. `profile` is the YAML profile represented as a JSON object. `preview: true` returns targets without creating jobs. Default operation is `audit`. Successful creation returns `batch_id` and `jobs`. Do not blindly retry schedule creation after a lost response; check the queue first.
- `POST check-in/`: `{"name":"checkmk-us","limit":3,"apply":true}`. Atomically returns due job assignments and private claim tokens. This is a claim, not a read-only poll, and is not blindly retried.
- `POST {id}/report/`: claim token and either `heartbeat: true`, or `sequence`, `stage`, `message`, optional `run_id` and `summary`. The worker uses `ready` as the synchronous start gate. Repeated/older event sequences cannot overwrite newer state.
- `POST {id}/cancel/`: cancel a pending job; or `{"recovered":true,"reason":"..."}` to release a verified recovery case.
- `GET /` and `GET {id}/`: current queue and progress for a dashboard. These endpoints never expose claim tokens and reject ordinary PATCH/DELETE operations.

- `POST {id}/hold/` and `POST {id}/release/`: `{"reason":"..."}`, required. Hold a pending job, or return a held one to the schedule. `POST release-batch/`: `{"batch_id":"<uuid>","reason":"..."}` releases every held job in the batch and returns the count. All three need `change` on upgrade jobs and a write-enabled token; a refusal from the queue is a `409` with the reason in `detail`.

Redundancy groups and dependencies are ordinary NetBox REST resources under `/api/plugins/discovery/`, with the usual list, create, `PATCH`, `DELETE`, tags and changelog:

- `upgrade-groups/`: `name`, `max_concurrent` (default 1; 0 for no limit), `members` (device ids), `depends_on` (ids of the groups this one waits for), `description`, `comments`, `tags`. Filters: `name`, `source` (`manual`, `fhrp`, `cable`), `stale`, `max_concurrent`, `member_id`, `depends_on_id`, `key` and `q`.
- `upgrade-dependencies/`: `upstream` (the device that waits) and `downstream` (the device that goes first), as device ids, plus `description`, `comments`, `tags`. Filters: `upstream_id`, `downstream_id`, `source`, `stale`, `key` and `q`.
- `source`, `key` and `stale` are read-only on both; anything created through the API is manual.
- `POST upgrade-groups/refresh/` with an empty body runs the same refresh as the button and returns `{"groups", "dependencies", "stale_groups", "stale_dependencies"}`. It needs `change` on redundancy groups and a write-enabled token.

```bash
# An F5 pair that must go one at a time (members are NetBox device ids)
curl -sS -X POST "$NETBOX_URL/api/plugins/discovery/upgrade-groups/" \
  -H "Authorization: Token $NETBOX_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "atl-f5-pair", "max_concurrent": 1, "members": [412, 413]}'

# Device 731 waits for device 655
curl -sS -X POST "$NETBOX_URL/api/plugins/discovery/upgrade-dependencies/" \
  -H "Authorization: Token $NETBOX_TOKEN" -H "Content-Type: application/json" \
  -d '{"upstream": 731, "downstream": 655}'
```

Deployment requires plugin migrations through `0008_upgrade_poller_last_seen` and the matching remote worker release. Lab validation of the exact switch/profile path is still required before scheduling production upgrades. Unit and isolated NetBox tests exercise coordination and failure handling; they do not substitute for a physical C9350 upgrade test.
