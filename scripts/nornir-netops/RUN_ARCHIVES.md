# JSON run archives

Every `configure.py` invocation writes a JSON archive automatically. This includes
configuration previews, applies, failed runs, discovery, checks, rollback and
selftest. The terminal prints the filename. No extra flag is required.

By default, files go in `<project>/reports/` with UTC timestamps and unique run IDs.
Set the location in `.env` for scheduled jobs:

```dotenv
NETOPS_REPORT_DIR=/var/lib/netops/reports
```

The executing account needs write access to that directory. CLI options override it:

```bash
./configure.py syslog --netbox --policy netbox --report-dir ./archives
./configure.py syslog --netbox --policy netbox --apply --yes --report-dir ./archives
./configure.py banner --netbox --report ./banner-results.json
```

`--report FILE` selects an exact filename and replaces an existing file at that
path. Automatic filenames avoid that overwrite. JSON files have owner-only
permissions (0600). The initial archive is created before accessing devices, each
configuration plan is checkpointed before its device writes, and completed device
results are checkpointed as they return. Updates use an atomic file replacement.
An archive write failure fails the run; failure to create the initial archive
prevents device processing. A forced process termination may leave a `running`
archive with unfinished device results. No program can finalize a file after
being forcibly killed or losing its filesystem.

## Structure

`schema_version: 2` identifies this format. Top-level fields include `run_id`,
`feature`, `started_at`, `finished_at`, `dry_run`, `exit_code`, `status`, `error`
and a `devices` object keyed by inventory name. Existing report fields remain
available for older consumers. Existing integration payloads (such as ServiceNow
attachments) retain their previous format; the local archive adds versioned
metadata and normalized steps. Timestamps use UTC ISO 8601.

Each device includes:

| Field | Meaning |
| --- | --- |
| `hostname`, `platform` | Connection address and selected platform. The object key is the inventory device name. |
| `action` | Effective action: `audit`, `add`, `manage`, or `rollback`. Legacy `replace` is normalized to `manage`. A conflicting NetBox policy has a null action and a device error. |
| `action_from_netbox` | True when the device's action was selected through NetBox policy, including untagged devices that default to audit. Merely sourcing inventory from NetBox does not make this true. |
| `action_source` | `netbox`, `cli`, `environment`, `default`, or `utility`. |
| `netbox_policy_tag` | The matching tag, such as `syslog-add`, or null when none selected the action. |
| `configuration_scope` | Feature whose configuration was read, including the original feature during rollback. |
| `current_config` | Original feature configuration and the read commands/API endpoints used to obtain it. |
| `implementation` | Ordered implementation, verification and persistence steps, whether changes are needed, and whether the invocation permits executing them. This is a plan; use result fields to determine what happened. |
| `backout` | Ordered restore, verification and persistence steps, a `complete` boolean, and limitations that require operator input. |
| `result_after` | Observed feature configuration after the change, application/verification/save results, execution status and any error. |

For example, the device portion of a NetBox-selected add preview contains:

```json
{
  "action": "add",
  "action_from_netbox": true,
  "action_source": "netbox",
  "netbox_policy_tag": "syslog-add",
  "configuration_scope": "syslog",
  "implementation": {
    "changes_needed": true,
    "will_execute": false,
    "steps": [
      {"transport": "ssh", "command": "logging host 192.0.2.50", "purpose": "configure"},
      {"transport": "ssh", "command": "show running-config | include ^logging", "purpose": "verify_config"},
      {"transport": "ssh", "command": "write memory", "purpose": "persist_config"}
    ]
  }
}
```

`--add` produces `action: "add"`, `action_from_netbox: false`, and
`action_source: "cli"`, even when inventory comes from NetBox. `--policy manage`
or `--replace` produces `action: "manage"`. A dry run retains the selected action:
`dry_run` and `implementation.will_execute` distinguish a preview from an apply.
An audit policy remains read-only for device configuration with `--apply`, though
that flag can permit the existing NetBox audit-field writeback.

For mixed policies, inspect each device. The top-level `action` is `mixed` when
devices have different actions; top-level `action_from_netbox` is true if any
device uses NetBox policy. An untagged device under `--policy netbox` records
`action: "audit"` and a null `netbox_policy_tag`.

## Configuration evidence and results

These are **feature snapshots**, not full-device backups. They retain the parsed
settings needed to explain and reverse the feature's changes: for example,
syslog destinations/source settings, complete parsed ACL or banner bodies, or F5
remote-server objects. Unrelated configuration is outside the snapshot's scope.
Discovery archives contain platform observations; `check-ntp` contains operational
check data; selftest has no device configuration. Failures before device access
have no device snapshots.

`current_config.status` is `observed` or `unavailable`. `result_after.status` is:

| Value | Meaning |
| --- | --- |
| `observed` | Configuration was read back. This alone does not mean it passed verification; inspect `verified`. |
| `not_changed` | No configuration write was attempted. `config` repeats the original observation and is not a second device read. |
| `unknown` | A write was attempted but no complete readback is available, including `--no-verify`, interruption or a failed write/read. `config` is null. Partial F5 observations may remain in the feature's legacy `profiles` or `banners` rows. |
| `unavailable` | No original or resulting configuration was obtained. `config` is null. |

`applied`, `verified`, and `saved` are separate. A rejected or timed-out request
can have partial effects without `applied` becoming true; `change_attempted` and
an `unknown` result preserve that uncertainty. `--no-save` omits persistence
steps. Successful exit alone does not mean initial compliance: a successful dry
run can still report required changes.

## Implementation and backout steps

SSH steps specify `command`; run configuration/restore commands in the device's
configuration session in their listed order, preserving interface and block
context. Verification and `write memory` steps run in privileged execution mode.
Banner commands include multiline text and should be sent without command-echo
verification, as the script does.

F5 steps specify HTTP `method`, `path` and JSON `body`, authenticated against the
device address using the existing BIG-IP REST login. For example:

```json
{
  "transport": "rest",
  "method": "PATCH",
  "path": "/mgmt/tm/sys/syslog",
  "body": {
    "remoteServers": [{"name": "original", "host": "192.0.2.99", "remotePort": 514}]
  },
  "purpose": "restore"
}
```

Backout steps restore the original settings affected by this plan. They are
available on dry runs as well as applies. Review the current device state before
replaying them: another operator or job may have changed it since the snapshot.
After restoration, read the feature again and compare with `current_config`.
`backout.complete: false` means the archive cannot establish a complete reversal;
read `limitations` before executing any steps.

Passwords, SNMP communities, tokens and protected key material are redacted.
Archives are not a secret backup. Steps needing new credentials contain redacted
placeholders; supply them from the same protected source used for the run. Some
original credentials cannot be read back from a device. F5 SNMP restoration lists
`requires_secret_fields` when old passphrases or a community must be supplied;
missing old settings also make backout incomplete. Irreversible operations retain
their feature-specific limitations.

The JSON archive is separate from `.rollback/` journals. `--no-rollback-file`
disables the journal only; it does not disable archival JSON. `configure.py rollback`
still consumes SSH journals, not these JSON archives. F5 REST restoration is
performed using the listed API steps. Rollback invocations read the affected
feature before and after applying; reapplying the original journal's forward
commands is recorded as an incomplete backout because intervening drift may
prevent an exact restoration.
