# Complete upgrade and configuration profile examples

These examples match the profile formats supported by this repository. The F5
upgrade uses the saved lab jobs from the overnight October 14–15, 2026 run.
Configuration-standard addresses, usernames and contact information below are
examples to replace with your own values. Credentials stay in the worker's
protected environment or secret store.

- [F5 upgrade used in the lab](#f5-upgrade-used-in-the-lab)
- [Saved configuration job profiles](#saved-configuration-job-profiles)
- [Complete standard definitions](#complete-standard-definitions)
- [Using the profiles](#using-the-profiles)
- [CLI-only features](#cli-only-features)

## Where each example goes

| Example | Destination |
| --- | --- |
| Upgrade YAML | A CLI `--profile` file, or a custom schedule's `profile` object after YAML-to-JSON conversion. |
| Saved profile JSON | `POST /api/plugins/discovery/job-profiles/`; the outer `name` and `kind` identify the saved profile, and `plan` holds its settings. |
| Standard definition YAML | **Config Compliance → Standards**, check type **Feature settings (YAML)**, definition field; create one standard per YAML block/section. |

A configuration job profile selects features and an add/replace policy. The
actual NTP servers, SNMP users and other desired values live in versioned
standards, which NetBox freezes into each scheduled job. Putting `ntp`, `syslog`
or credentials directly inside a remediation `plan` is not supported.

## F5 upgrade used in the lab

The frozen profiles on jobs **13**, **18** and **25** are identical. This is the
complete effective profile, including explicit values for the four defaults
that were omitted from those saved jobs: `bundle_conversion_validated`,
`volume`, `license_check_date` and `skip_running_config_check`.

```yaml
name: F5 Lab - 17.5.1.9 to 17.5.1.10
models:
  - BIG-IP Virtual Edition
starting_versions:
  - 17.5.1.9
target_version: 17.5.1.10
image: BIGIP-17.5.1.10-0.0.35.iso
md5: 0fc6d3549c3439f47ce00c1828edda96
minimum_free_bytes: 1500000000
bundle_conversion_validated: false
image_source: https://10.50.10.162/images/BIGIP-17.5.1.10-0.0.35.iso
volume: ''
allow_active: false
ucs_backup: true
license_check_date: ''
drain_connections: true
drain_min_reduction_percent: 5
drain_window_seconds: 60
drain_timeout_seconds: 900
skip_running_config_check: false
```

| Job | Device | Result | What it exercised |
| --- | --- | --- | --- |
| 13 | f5-lab03 | Completed with warnings | Reused an already installed target on HD1.1, copied current configuration, rebooted and validated. |
| 18 | f5-lab03 | Completed with warnings | Fresh installation after the inactive HD1.1 volume was removed. |
| 25 | f5-lab01 | Completed | The same approved image/profile through the worker. |

These jobs completed on October 15, 2026 UTC. Lab03's license warning was
already present before the upgrade. This profile does not establish a release
path for other F5 models or versions.

- The checksum is the value used and verified in these lab runs. It belongs to
  the exact ISO filename shown above.
- The image URL is the lab's image server. Use a worker-accessible URL or local
  image path for another environment.
- `volume: ''` deliberately lets the worker select an inactive volume or create
  one. HD1.1 was the target on lab03; it was not pinned in the profile.
- `allow_active: false` does not prevent a standalone upgrade. It prevents
  automatic failover of an active HA member. Connection draining currently
  supports standalone units only.
- `license_check_date: ''` leaves the target service-check-date requirement
  unset, matching the lab. A production profile should use the date verified
  for its exact target release.
- `drain_min_reduction_percent: 5` means draining can finish when the reduction
  over a full 60-second observation window falls below 5%; it does not mean
  “wait until only 5% of the original connections remain.” Zero connections can
  finish immediately; the maximum drain wait is 900 seconds.
- `skip_running_config_check: false` retains the running-config comparison.

The current worker saves disabled VIP states before installing, boots with them
disabled, requires two clean validation passes (including WAF readiness/policy
checks when applicable), then restores and saves their original states. That
ordering was revised after the above jobs started; these historical results
should not be treated as a live test of every subsequent drain/WAF change.

### Save the F5 profile through the API

This complete request creates an **unassigned draft**. Set `device_types` to the
actual NetBox device-type IDs to assign it, or select **Assigned models** in the
UI afterward. NetBox derives `models` from those assignments; the API ignores a
`plan.models` list. No device is scheduled by creating this profile.

```json
{
  "name": "F5 Lab - 17.5.1.9 to 17.5.1.10",
  "kind": "upgrade",
  "device_types": [],
  "description": "Lab BIG-IP VE upgrade from 17.5.1.9 to 17.5.1.10 with UCS backup and standalone connection draining.",
  "plan": {
    "name": "F5 Lab - 17.5.1.9 to 17.5.1.10",
    "starting_versions": [
      "17.5.1.9"
    ],
    "target_version": "17.5.1.10",
    "image": "BIGIP-17.5.1.10-0.0.35.iso",
    "md5": "0fc6d3549c3439f47ce00c1828edda96",
    "minimum_free_bytes": 1500000000,
    "bundle_conversion_validated": false,
    "image_source": "https://10.50.10.162/images/BIGIP-17.5.1.10-0.0.35.iso",
    "volume": "",
    "allow_active": false,
    "ucs_backup": true,
    "license_check_date": "",
    "drain_connections": true,
    "drain_min_reduction_percent": 5,
    "drain_window_seconds": 60,
    "drain_timeout_seconds": 900,
    "skip_running_config_check": false
  }
}
```

In the web form, choose **Upgrade**, assign **BIG-IP Virtual Edition**, and
paste the upgrade settings. The form owns these controls separately: leave
**Skip running-config matching** unchecked; check **Drain F5 connections before
reboot** and set reduction **5**, window **60**, maximum wait **900**. YAML values
for the drain controls are replaced by the form controls when saving.

### Image staging without installation

Use the same complete F5 profile above with operation **Stage software** in
NetBox, or `--stage-only` in the CLI. No alternate upgrade profile is required.
Staging uploads/verifies the image; it does not install, drain, take an upgrade
UCS or reboot. `starting_versions: []` is allowed for a staging-only custom
profile, but keep the explicit starting version when reusing a saved upgrade
profile.

## Saved configuration job profiles

Each JSON block below is a complete saved-profile request. In the UI, choose
**Remediation**, enter its name, and select the matching features and mode.
`replace` checks/enforces the desired set; `add` preserves extra entries where
the feature supports that distinction. A profile alone never starts writes:
**Configuration audit** uses `audit_config`, while **Remediate configuration**
uses `remediate` and requires an applying worker plus the appropriate permissions.

### Platform coverage for these examples

| Platform | Bundle/features below |
| --- | --- |
| Cisco IOS/IOS XE | NTP, syslog, banner, ACL and SNMP. Packet size and local accounts currently require the CLI; see below. |
| Arista EOS | NTP, syslog, banner, ACL and SNMP. Local accounts currently require the CLI. |
| Cisco NX-OS | `ntp`, `syslog`. |
| F5 BIG-IP | `ntp`, `syslog`, `banner`, `snmp`. |
| Juniper Junos | `ntp`. |
| ArubaOS / ClearPass | NTP adapters exist; conductor/cluster handling has additional requirements in the main README. |
| Palo Alto / Fortinet | No NTP deployment adapter in this worker; do not assign an NTP example expecting those platforms to apply it. |

### F5 baseline standards

Use the basic NTP, syslog, banner and SNMPv3 definitions below. The F5 SNMP
standard must omit `packetsize`; packet-size configuration is Cisco-only.

```json
{
  "name": "F5 baseline standards",
  "kind": "remediate",
  "plan": {
    "features": [
      "ntp",
      "syslog",
      "banner",
      "snmp"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

### Cisco IOS/IOS XE baseline standards

Use the SNMP definition without packet size and the other matching
definitions. Select all five listed features together.

```json
{
  "name": "Cisco baseline standards",
  "kind": "remediate",
  "plan": {
    "features": [
      "ntp",
      "syslog",
      "banner",
      "acl",
      "snmp"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

### Arista EOS baseline standards

Use the SNMP definition without packet size.

```json
{
  "name": "Arista baseline standards",
  "kind": "remediate",
  "plan": {
    "features": [
      "ntp",
      "syslog",
      "banner",
      "acl",
      "snmp"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

### NX-OS time and logging

```json
{
  "name": "NX-OS time and logging",
  "kind": "remediate",
  "plan": {
    "features": [
      "ntp",
      "syslog"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

### NTP only

This profile can also be assigned to Junos. Use one of the NTP definitions
below, scoped to the appropriate platforms; do not overlap both definitions.

```json
{
  "name": "NTP standard",
  "kind": "remediate",
  "plan": {
    "features": [
      "ntp"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

### System syslog only

```json
{
  "name": "System syslog standard",
  "kind": "remediate",
  "plan": {
    "features": [
      "syslog"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

### Banners only

```json
{
  "name": "Login banner standard",
  "kind": "remediate",
  "plan": {
    "features": [
      "banner"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

### SNMPv3 only

Pair with the SNMP definition that omits `packetsize`.

```json
{
  "name": "SNMPv3 standard",
  "kind": "remediate",
  "plan": {
    "features": [
      "snmp"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

### SNMP poller ACL only

The ACL definition below is self-contained and does not require an SNMP
standard to resolve its permit list.

```json
{
  "name": "SNMP poller ACL",
  "kind": "remediate",
  "plan": {
    "features": [
      "acl"
    ],
    "mode": "replace",
    "allow_clearpass_cluster_changes": false
  }
}
```

## Complete standard definitions

Create one standard for each applicable section, set its platform/site/role/tag
scope and effective dates, and select **Auto remediable** for writes. `replace`
also requires **Allow enforce**. Audits do not require those write flags.
Only one in-force definition for a section may apply to a device. In particular,
choose one NTP variant and one SNMP variant per device.

The basic examples intentionally omit source interfaces and VRFs. On supported
platforms, NetBox's existing source-interface tags can still supply a source;
omitting it here does not remove those inventory tags or clear existing source
configuration.

### Basic NTP

For the F5 baseline and other supported NTP adapters. The addresses are examples,
not the lab's recorded NTP settings.

```yaml
ntp:
  servers:
    - 192.0.2.10
    - 192.0.2.11
```

### Authenticated NTP for Cisco IOS/IOS XE and Arista EOS

Keep the existing key ID and key material when reusing a deployed key. `1` is an
example ID, not a record of the fleet's key ID. Supply its material through
`NETOPS_NTP_KEY_1` (or the corresponding ID) on the worker. Use the basic profile
for F5; this block does not turn on F5 NTP authentication. Key rotation requires
an explicit `--rewrite-keys` CLI run because encrypted material cannot be
compared. Saved queued profiles do not expose that rotation flag.

```yaml
ntp:
  servers:
    - 192.0.2.10
    - 192.0.2.11
  prefer: 192.0.2.10
  iburst: true
  authentication:
    key_id: 1
    type: md5
    trusted: true
    enable: true
```

### System syslog

F5 manages the remote destinations. Severity/origin options are applied only
where the platform supports them; EOS ignores Cisco's `origin_id` setting.
F5 system syslog and WAF logging are separate operations.

```yaml
syslog:
  destinations:
    - host: 192.0.2.50
      port: 514
    - host: 192.0.2.51
      port: 1514
  severity: informational
  origin_id: hostname
```

### Login/MOTD banner

The full selectable settings are below. Banner text comes from the checked-in
`templates/<platform>/banner.j2` file; a `text` property is not supported in the
standard. Review that template when adopting this standard. F5 uses the selected
text for both SSH and web login banners.

```yaml
banner:
  motd: true
  login: false
  delimiter: ^
```

### SNMPv3 without packet size (F5, IOS/IOS XE, EOS)

Provide `NETOPS_SNMP_AUTH_NMSUSER` and `NETOPS_SNMP_PRIV_NMSUSER` in the worker's
protected environment, or configure its SNMP secret store. No passwords belong
in this YAML. Existing-user credential rotation needs the explicit CLI
`--rewrite-users` option.

On IOS/EOS, create the matching poller ACL before binding it; the ACL definition
is below. F5 applies `allow` directly to its client allow list, adds localhost,
and maps the group/view onto its user settings. F5 does not configure the trap
`hosts` or bind Cisco group ACL names. `communities: []` removes communities
under replacement; on F5 it also disables v1/v2c responses.

```yaml
snmp:
  allow:
    - 192.0.2.50/32
    - 198.51.100.0/24
  acl: SNMP-POLLERS
  communities: []
  location: Example site - network room
  contact: netops@example.com
  views:
    - name: NMS-VIEW
      oid: iso
      action: included
  groups:
    - name: NMS-RO
      security: priv
      read: NMS-VIEW
      acl: SNMP-POLLERS
  users:
    - name: nmsuser
      group: NMS-RO
      auth: sha
      priv: aes 128
  hosts:
    - host: 192.0.2.50
      user: nmsuser
      security: priv
```

### SNMP packet size: schema example and current execution limitation

NetBox accepts this complete packet-size-only standard and the profile feature
`snmp_packetsize`. The worker currently passes that underscore spelling to the
CLI, whose command is `snmp-packetsize`, so queued packet-size jobs fail before
connecting. The CLI also takes the value from `--size`, not from this YAML.
The ready-to-use platform bundles above omit this feature.

```yaml
snmp:
  packetsize: 1300
```

For the supported IOS/IOS XE CLI route, provide the size explicitly:

```bash
./configure.py snmp-packetsize --size 1300 --netbox --netbox-filter name=example-ios-switch --replace
```

Do not add `packetsize` to the queued SNMPv3 definition above: NetBox would then
require both `snmp` and `snmp_packetsize`, exposing the same worker limitation.
EOS and F5 do not have this packet-size deployment feature.

### SNMP poller ACL (IOS/IOS XE, EOS)

A complete, standalone permit list for the `SNMP-POLLERS` ACL referenced above.
`rebuild: true` permits deleting and rebuilding this ACL to correct ordering;
use this example for a polling ACL, not a VTY or traffic-filtering ACL.

```yaml
acls:
  - name: SNMP-POLLERS
    type: standard
    permit:
      - 192.0.2.50/32
      - 198.51.100.0/24
    deny_log: true
    rebuild: true
```

### Local accounts: schema example and current execution limitation

NetBox accepts the following complete standard definition and a profile with
`features: [users]`, but the current CLI requires `--user` and does not consume
`local_accounts`. The queued worker does not supply those arguments, so a
queued users job fails before connecting. This definition records the intended
shape; it is not a working queued deployment recipe yet. The platform bundles
above omit `users` for that reason.

```yaml
local_accounts:
  names:
    - admin
    - netauto
  privilege: 15
  role: network-admin
```

For the supported CLI route, provide `NETOPS_PW_ADMIN` and `NETOPS_PW_NETAUTO`
in the protected worker environment or use its password secret store. These are
complete preview commands; account names, privilege and EOS role are explicit:

```bash
# Cisco IOS/IOS XE
./configure.py users --user admin,netauto --privilege 15 --netbox --netbox-filter name=example-ios-switch --add
# Arista EOS
./configure.py users --user admin,netauto --privilege 15 --role network-admin --netbox --netbox-filter name=example-eos-switch --add
```

`add` preserves unrelated local accounts. Managed accounts are still rewritten
with their supplied passwords; this is account rotation, not a readback
comparison of hashes. The worker protects its session account and the last
privilege-15 account.

## Using the profiles

1. Save the configuration definitions with non-overlapping scopes and the
   appropriate audit/remediation permissions.
2. Create a saved profile from a complete JSON example, or enter its values in
   **Device Operations → Job Profiles**.
3. Assign a remediation profile to a platform or model. A model remediation
   profile overrides its platform profile; the two feature lists are not merged.
   Upgrade profiles require model assignments.
4. Preview a **Configuration audit** before scheduling remediation. For upgrades,
   use a pre-upgrade audit, then staging, then the approved upgrade window.
5. Review the pinned profile and standard revisions on the resulting job.
   Editing a saved profile affects future jobs, not jobs already queued.

For CLI use, save the upgrade YAML as `f5-lab-upgrade.yaml`. Save the desired
standard definitions into one local `standards.yaml` with exactly one of each
section. The following commands omit `--apply`, so they inspect/preview only:

```bash
./configure.py upgrade --profile f5-lab-upgrade.yaml --netbox --netbox-filter name=f5-lab03
./configure.py upgrade --profile f5-lab-upgrade.yaml --netbox --netbox-filter name=f5-lab03 --stage-only
./configure.py ntp --standards standards.yaml --netbox --netbox-filter name=f5-lab03 --replace
```

Worker credentials, TLS policy, UCS directory, concurrency, install/reload
limits and validation deadlines are worker/CLI settings, not extra profile
properties. See [UPGRADES.md](UPGRADES.md) and
[SCHEDULED_UPGRADES.md](SCHEDULED_UPGRADES.md) for those settings and the full
scheduling API.

## CLI-only features

The CLI also supports `waf` and `nac`. Neither is currently a valid feature in a
saved configuration job profile. Do not add them to a queued `features` list.

### F5 WAF remote logging

Use the complete syslog definition above as the desired destination list, then
run `configure.py waf` against the selected F5. It manages eligible user-created
remote application logging profiles; it does not create WAF security policies
or configure system syslog. Preview example:

```bash
./configure.py waf --standards standards.yaml --netbox --netbox-filter name=f5-lab01 --replace
```

### NAC port configuration (IOS/IOS XE, EOS)

This is a complete CLI standard section. Put it in a local standards file; it
is not accepted as a versioned NetBox YAML standard. The required per-port
commands live in `templates/<platform>/nac.j2`.

```yaml
nac:
  policy: NAC-POLICY
  scope:
    access_only: true
    skip_shutdown: true
    exclude:
      - TenGigabitEthernet
      - FortyGigabitEthernet
    exclude_description:
      - uplink
      - trunk
      - access point
```

```bash
./configure.py nac --standards standards.yaml --netbox --netbox-filter name=example-access-switch
```

For more platform details and credential setup, see [README.md](README.md),
[CLI_REFERENCE.md](CLI_REFERENCE.md) and [standards.yaml.example](standards.yaml.example).
