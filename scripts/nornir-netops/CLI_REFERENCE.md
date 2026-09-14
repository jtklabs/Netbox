# Command and flag reference

Run commands from `scripts/nornir-netops` (or the directory where the tool is
installed). Put flags **after the subcommand**:

```bash
./configure.py syslog --netbox --limit core-sw1 --policy manage
./configure.py syslog --netbox --limit core-sw1 --policy manage --apply
```

The first command previews the changes; the second applies them. There is no
`--dry-run` flag: leaving off `--apply` is the dry run. `--yes` alone does not
apply anything. Examples assume inventory, credentials and required standards
are already configured. See the [README](README.md) for installation and
standards-file examples.

## Contents

- [Feature flags](#feature-flags): [NTP](#ntp), [syslog](#syslog), [WAF](#waf),
  [banner](#banner), [SNMP](#snmp), [SNMP packet size](#snmp-packetsize),
  [ACL](#acl), [NAC](#nac), [local accounts](#users).
- [Utility commands](#utility-commands): [NTP check](#check-ntp),
  [discovery](#discover), [rollback](#rollback), [selftest](#selftest).
- [IOS XE upgrades](UPGRADES.md): approved profiles, NetBox targeting, install
  mode/conversion, parallel execution, pre/post validation and progress webhooks.
- [Shared flags](#shared-flags): [inventory](#inventory-and-device-selection),
  [NetBox](#netbox-inventory-and-interface-tags), [authentication](#device-authentication),
  [AWS](#aws-secrets-manager), [F5 HTTPS](#f5-https),
  [standards and environment](#standards-and-environment-files),
  [execution](#execution-modes), [reports](#output-and-reports),
  [rollback files](#rollback-journals), [platform cache](#platform-cache),
  [ServiceNow](#servicenow-change-control).
- [Environment-only settings and precedence](#environment-only-settings-and-precedence).
- [Recipes](#recipes) and [exit codes](#exit-codes).

## Feature flags

The supported platform families are Cisco IOS/IOS-XE (including Catalyst 3850
and 9300), Arista EOS and F5 BIG-IP. Support varies by feature:

| Feature | Cisco | Arista | F5 |
| --- | --- | --- | --- |
| `ntp` | Yes | Yes | No |
| `syslog` | Yes | Yes | System collectors |
| `waf` | No | No | Existing custom remote logging profiles |
| `banner` | Login/MOTD | Login/MOTD | SSH and web login |
| `snmp` | Users, groups, views, hosts, communities | Same; no group ACL binding | Users, client allow list, v1/v2c disabling |
| `snmp-packetsize` | Yes | Skipped | No |
| `acl` | Yes | Yes | No |
| `nac` | Yes | Yes | No |
| `users` | Yes | Yes | No |
| `check-ntp` | Yes | Yes | No |

Feature commands accept the [shared flags](#shared-flags). F5 HTTPS flags are
available only on `waf`, `syslog`, `snmp` and `banner`. A flag being accepted by
the parser does not mean every platform implements that setting; the details
below identify the differences.

### ntp

Converge NTP servers and the authentication settings defined in
`ntp.authentication`.

| Flag | Meaning and default |
| --- | --- |
| `-s`, `--servers ADDR[,ADDR...]` | Desired server list. Repeatable; replaces `ntp.servers` from the standards file. |
| `--vrf NAME` | VRF for server configuration. Defaults to `ntp.vrf`; otherwise no VRF. |
| `--prefer ADDR` | Mark a desired server as preferred. It must be in the desired list. Otherwise uses the preference defined with `ntp.servers` or `ntp.prefer`. |
| `--source INTERFACE` | Source interface. Defaults to `ntp.source`; NetBox's per-device interface selection takes precedence over both. |
| `--no-iburst` | Omit `iburst` from the Arista template. The flag defaults off, so Arista normally gets `iburst`. |
| `--key-secret NAME_OR_ARN` | AWS secret containing NTP key material as `{"<key id>": "<material>"}`. Default: `NETOPS_NTP_SECRET`. |
| `--rewrite-keys` | Push key material again even when the key already exists. Needed for rotation because encrypted material cannot be compared. Default: off. |

Authentication key IDs, types and enablement come from `ntp.authentication`.
Material can also come from `NETOPS_NTP_KEY_<ID>`. NetBox source selection uses
the tagged interface; no matching tag supplies no source interface, and multiple
matching interfaces fail that device. `ntp.iburst` is not currently read by the
feature; use `--no-iburst` to control that behavior.

```bash
./configure.py ntp --netbox --servers 10.1.1.50,10.1.1.51 --replace
```

### syslog

Converge regular syslog collectors on Cisco, Arista and F5. F5 manages collector
IPs and ports; switch severity, source, origin ID and VRF settings do not apply
to its REST configuration.

| Flag | Meaning and default |
| --- | --- |
| `-d`, `--destination ADDR[:PORT]` | Desired collectors; repeatable and comma-separated. Overrides `syslog.destinations`; default port 514. Bare IPv6 uses port 514; for a custom IPv6 port, use a `{host, port}` entry in `syslog.destinations`. F5 requires literal IPs. |
| `--severity LEVEL` | Override `syslog.severity`. Choices: `emergencies`, `alerts`, `critical`, `errors`, `warnings`, `notifications`, `informational`, `debugging`. Applies to switches. |
| `--source INTERFACE` | Override `syslog.source` for CSV/direct inventory. NetBox source-interface tags take precedence. Applies to switches. |
| `--origin-id VALUE` | Cisco message identifier: `hostname`, `ip`, `ipv6`, or a literal string. Defaults to `syslog.origin_id`. Arista/F5 do not configure it. |
| `--syslog-source-tag TAG` | NetBox interface tag identifying the switch's source interface. Default: `NETBOX_SYSLOG_SOURCE_TAG`, then the configured `syslog` source tag, then `syslog-source`. |
| `--policy audit\|add\|manage\|netbox` | Select the logging action; see the policy table below. |
| `--netbox-checked-field NAME` | NetBox device datetime field for the last completed logging check. Default: `NETBOX_CHECKED_FIELD`, then `syslog_last_checked`. |

There is no syslog `--vrf` flag. Set `syslog.vrf` in the standards file for
switches. No tagged source interface means the existing source is left alone;
multiple matches fail the device before configuration.

#### Logging policies

These choices apply to **both `syslog` and `waf`**, not to other features.

| Policy | Behavior |
| --- | --- |
| `audit` | Compare against the fully managed state. Never change device configuration, even with `--apply`. |
| `add` | Add missing destinations and correct managed settings, preserving extra destinations. |
| `manage` | Add missing destinations and remove extra destinations; enforce the managed settings. |
| `netbox` | Resolve the policy separately for every device from its NetBox tags. Requires NetBox inventory. |

For `netbox`, `syslog-audit`, `syslog-add` and `syslog-manage` select the
corresponding action. Untagged devices are audit-only. Conflicting policy tags
fail that device. An explicit `--policy add`, `--policy manage` or
`--policy audit` overrides the tags for all selected devices.

Resolution order: explicit `--policy` or explicit `--add`/`--replace`, then
`NETOPS_SYSLOG_POLICY`, then legacy `NETOPS_F5_POLICY`, then `netbox` for NetBox
inventory or `add` otherwise. `--replace` is the legacy equivalent of
`--policy manage`; `--add` selects add. Do not combine either with `--policy`.

**Policy and apply are independent.** `--policy manage` without `--apply`
previews the managed configuration. With NetBox inventory, `--apply` can write
logging audit fields even under `--policy audit`; without it, NetBox writes are
only previewed. The boolean field is always `syslog_compliant`, indicating
whether the observed configuration matches the fully managed standard. For
F5 each run evaluates only its selected feature: system syslog or eligible WAF
destinations. The existing NetBox fields describe the most recent logging check;
use the JSON archive's feature scope to distinguish their results. Failed or
incomplete checks leave the applicable audit fields unchanged.

```bash
./configure.py syslog --netbox --policy netbox
./configure.py syslog --netbox --policy netbox --apply --yes
```

### waf

F5 only. Reconcile remote destinations in confirmed user-created WAF logging
profiles that already have remote logging configured. Built-in profiles such
as internal remote logging and cloud security service profiles are excluded;
unknown ownership is not treated as permission to change a profile.

| Flag | Meaning and default |
| --- | --- |
| `--policy audit\|add\|manage\|netbox` | Same [logging policy](#logging-policies) rules as `syslog`. |
| `--netbox-checked-field NAME` | Same datetime field selection as `syslog`. |

Use the [F5 HTTPS flags](#f5-https) for the connection. Destinations come from
`syslog.destinations` in the standards file; **WAF has no `--destination`
override**. It does not create new remote logging profiles. WAF and system
syslog independently read, configure, verify and report their own sections.
Neither audits or requires access to the other, and regular syslog does not
query ASM provisioning. They share standards destinations, NetBox policy tags and audit
field names; compliance is calculated only for the selected feature.
Neither operation triggers ConfigSync.

```bash
./configure.py waf --netbox --limit bigip1 --policy manage --apply
```

### banner

Apply the selected login/MOTD text. Selection comes from the `banner` section;
the text itself is edited in `templates/<platform>/banner.j2`.

| Flag | Meaning and default |
| --- | --- |
| `-b`, `--banner motd\|login` | Select one kind; repeat to select both. Overrides `banner.motd` / `banner.login`. At least one must be selected. |

F5 also accepts the [HTTPS flags](#f5-https). It applies selected text to both
SSH and web login. If both kinds are selected, F5 joins them with a blank line.
F5 add/replace manage the same two login surfaces. On switches, replace also
removes unselected banner kinds. Logging policy tags do not control banners.

```bash
./configure.py banner --netbox --banner motd --apply
```

### snmp

Use `snmp` settings from the standards file. V3 users and protocols are defined
there; passphrases come from the secret sources below.

| Flag | Meaning and default |
| --- | --- |
| `--passphrase-secret NAME_OR_ARN` | AWS secret shaped as `{"nmsuser": {"auth": "...", "priv": "..."}}`. Default: `NETOPS_SNMP_SECRET`. |
| `--location TEXT` | Override `snmp.location`. Quote text containing spaces. |
| `--contact TEXT` | Override `snmp.contact`. |
| `--rewrite-users` | Push managed users even if their readable settings match. Needed for changed passphrases or an unreadable user ACL. Switches recreate users; F5 updates them in place. Default: off. |
| `--f5-no-localhost` | F5 only: omit the implicit `127.0.0.0/8` client allowance. Combine with `--replace` to remove it if already configured. Default: off. |

Passphrases can also come from `NETOPS_SNMP_AUTH_<USER>` and
`NETOPS_SNMP_PRIV_<USER>`; sanitize the username to uppercase with non-alphanumeric
characters replaced by `_`. Values in the selected AWS secret take precedence
over those environment variables; missing values fall back to the environment,
then an interactive prompt. Passphrases are needed even for a dry run.

`snmp.communities: []` removes communities **even in add mode**. On F5 it also
disables v1/v2c responses. Omitting that key leaves community settings alone.
F5 requires `snmp.allow` and maps it to the global client allow list; it does
not bind Cisco group/user ACL names. F5 does not manage trap destinations or
chassis ID. Arista does not bind group ACLs; its control-plane restrictions
remain a separate configuration task. F5 accepts the [HTTPS flags](#f5-https).

```bash
./configure.py snmp --netbox --replace
./configure.py snmp --netbox --replace --rewrite-users --apply
```

### snmp-packetsize

Cisco only. Arista is explicitly skipped; F5 is unsupported.

| Flag | Meaning and default |
| --- | --- |
| `--size BYTES` | Maximum SNMP response size, 484–17940 bytes. Default: 1300. |

The current command takes its size from this flag/default, not
`snmp.packetsize` in the standards file.

```bash
./configure.py snmp-packetsize --netbox --size 1300 --apply
```

### acl

Manage named IPv4 access lists from `acls` in the standards file on Cisco and
Arista. Rule order matters; this is not an unordered set of permit statements.

| Flag | Meaning and default |
| --- | --- |
| `-a`, `--acl NAME` | Select an ACL from the standards file. Repeat for several. Default: all defined ACLs. |

ACL definitions, permitted networks, logging and rebuild behavior are file
settings, not CLI flags. See [ACL behavior](README.md#acls), especially the
definition's `rebuild` option before relying on add mode to preserve rules.

```bash
./configure.py acl --netbox --acl SNMP-POLLERS --replace
```

### nac

Audit/fix in-scope switch access ports for the NAC standard. The port commands
live in each platform's `nac.j2` template. Scope rules live under `nac.scope`.

| Flag | Meaning and default |
| --- | --- |
| `--policy NAME` | Subscriber control policy name; defaults to `nac.policy`. **This is a policy name, not the syslog action selector.** |
| `--include-trunks` | Include trunk ports in the audit/configuration scope. Default: excluded. |
| `--include-shutdown` | Include administratively shut ports. Default: excluded. |
| `--sync-netbox` | Save NAC status, findings, remediation and timestamps to NetBox interfaces. Also writes during audit-only runs. Defaults to `NETOPS_NAC_NETBOX_SYNC=false`. |
| `--no-sync-netbox` | Disable NAC NetBox writes even when enabled in `.env`. |

SVIs, port channels, loopbacks, tunnels and management interfaces are excluded.
These flags expand which physical ports are considered; review a dry run before
applying to the broader scope.

```bash
./configure.py nac --netbox --netbox-filter site=atl
./configure.py nac --netbox --sync-netbox
```

`--sync-netbox` publishes assessment results; `--apply` enables switch changes.
See [NAC NetBox reporting](README.md#save-nac-results-to-netbox-interfaces) for
the interface fields, scheduled-run configuration and CSV export template.

### users

Create/rotate local accounts on Cisco and Arista. Managed-account passwords are
different from the login password used to connect to the device.

| Flag | Meaning and default |
| --- | --- |
| `-U`, `--user NAME[,NAME...]` | **Required.** Managed local account names; repeatable or comma-separated. This is not `--username`. |
| `--privilege LEVEL` | Managed account privilege. Default: 15. |
| `--role NAME` | EOS role, for example `network-admin`. Default: no explicit role. |
| `--algorithm md5\|sha256\|scrypt` | Cisco algorithm for plaintext passwords; `scrypt` produces type 9. Default: device's algorithm. |
| `--hash-type TYPE` | Treat the supplied account value as a precomputed hash. IOS types: 5, 8, 9; EOS: 5, `sha512`. Otherwise it is plaintext. |
| `--password-secret NAME_OR_ARN` | AWS JSON mapping `{username: password}` for the managed accounts. Default: `NETOPS_PW_SECRET`. |
| `--only-missing` | Create absent accounts without rotating existing passwords. An SSH key on a managed account is still removed. Default: off. |
| `--allow-remove-self` | With replace, permit removal of the login account when it is not in the desired account list. Default: protect it. |

Environment account passwords use `NETOPS_PW_<UPPERCASE_USER>`, replacing
non-alphanumeric username characters with `_`; see the
[local account examples](README.md#local-users) for naming and secret formats.
The current command requires `--user`; it does not load account names, privilege
or role from `local_accounts` in the standards file. Default behavior rotates
managed accounts because existing password values cannot be compared.

```bash
./configure.py users --netbox --user automation --only-missing --apply
```

## Utility commands

### check-ntp

Read NTP operational state on Cisco/Arista; does not configure devices.

| Flag | Meaning and default |
| --- | --- |
| `-s`, `--servers ADDR[,ADDR...]` | Expected peers, repeatable/comma-separated. Defaults to `ntp.servers`. |
| `--max-offset MS` | Warn when the selected peer exceeds this absolute offset in milliseconds. Default: 1000. |

Accepts shared connection, inventory, logging and standards flags, but not
`--apply`, `--fail-on-diff` or ServiceNow flags. It returns 2 for
an attention result without needing a separate fail-on-diff option.

```bash
./configure.py check-ntp --netbox --max-offset 100
```

### discover

Detect missing platform values over SSH and store them in the platform cache.
It does not configure devices. F5 REST features should use explicit F5 platform
data in NetBox/CSV or `--platform f5_tmsh` for a direct address.

| Flag | Meaning and default |
| --- | --- |
| `--refresh` | Ignore cached detection and detect again. Explicit inventory platform values still win. Default: off. |

Accepts shared connection, inventory, logging, cache and standards flags.
Does not accept apply or ServiceNow flags. JSON archive flags are accepted. `--no-platform-cache` disables
cache use; unlike `--refresh`, it also prevents remembering the result.

```bash
./configure.py discover --csv inventory/hosts.csv --refresh
```

### rollback

Replay restorable SSH commands recorded in a previous journal. Devices and
addresses come from that journal, not current CSV/NetBox inventory.

| Argument/flag | Meaning and default |
| --- | --- |
| `journal` | Optional positional path. Default: most recent journal in the rollback directory. |
| `--rollback-dir DIR` | Directory searched when no journal is named. Defaults to `NETOPS_ROLLBACK_DIR`, then `<project>/.rollback`. |
| `--limit NAME[,NAME...]` | Select exact device keys **from the journal**. Unlike ordinary feature selection, this does not match management addresses. |
| `--apply` | Replay the commands. Without it, print the reversal. |
| `-y`, `--yes` | Skip the interactive confirmation. |
| `--no-save` | Leave the restored running configuration unsaved. Default: save. |

Uses the shared login/AWS/environment, SSH port/timeout, worker and logging
flags. Inventory/filter/platform flags inherited in help do not select or
replace journal devices. It does not accept `--standards`,
`--no-verify`, `--add`, `--replace` or ServiceNow flags. F5 REST rollback is
manual; consult the feature's report and README instructions.

```bash
./configure.py rollback .rollback/EXISTING-JOURNAL.json --limit core-sw1
./configure.py rollback .rollback/EXISTING-JOURNAL.json --limit core-sw1 --apply
```

### selftest

`./configure.py selftest` runs offline template/sample checks. It has no
feature flags; it accepts `-h` / `--help`, `--report` and `--report-dir`. It finds a standards file
in the normal locations and may fall back to the shipped example for this
offline check. It does not test live authentication, device APIs or reachability.

## Shared flags

These groups apply to configuration features unless stated otherwise.
`-h`, `--help` works on every subcommand and lists the flags it accepts:

```bash
./configure.py --help
./configure.py snmp --help
```

### Inventory and device selection

| Flag | Meaning and default |
| --- | --- |
| `-c`, `--csv FILE` | Read CSV inventory. File fallback: `NETOPS_CSV`, then `inventory/hosts.csv`. Explicit selection overrides `NETOPS_INVENTORY`. |
| `--netbox` | Read NetBox inventory. Also selected by `NETOPS_INVENTORY=netbox` when no source flag is given. |
| `--ip ADDRESS` | Target one literal IPv4/IPv6 address. Does not query NetBox or read CSV. |
| `--platform PLATFORM` | Platform for a direct-IP target: `cisco_ios`, `arista_eos`, `f5_tmsh` (recognized aliases also work). Otherwise switches can autodetect. Does not override CSV/NetBox platform data. |
| `--limit NAME[,NAME...]` | Restrict loaded inventory by exact device name or management address. Comma-separated, case-sensitive; not a wildcard or DNS search. |
| `--filter COLUMN=VALUE` | Filter loaded host metadata by exact string equality. Repeatable, ANDed. Works with CSV extra columns or mapped NetBox metadata such as site/role/platform-independent tags data. |

Choose only one of `--csv`, `--netbox`, `--ip`. Without an explicit choice,
`NETOPS_INVENTORY` selects `csv` (default) or `netbox`. NetBox defaults to active
devices with a primary IP. `--netbox-filter` restricts the API results first;
`--netbox-autofilter` can then restrict ownership to the local poller;
`--limit` and `--filter` further narrow the loaded inventory. Multiple filters
are intersections, not independent batches. For individual NetBox tags use
`--netbox-filter tag=TAG`; local `--filter tags=...` compares the whole stored
comma-separated tag string.

### NetBox inventory and interface tags

| Flag | Meaning and default |
| --- | --- |
| `--netbox-url URL` | NetBox base URL. Fallback: `netbox.url` in standards, then `NETBOX_URL`, then the selected NetBox secret's `url`. |
| `--netbox-secret NAME_OR_ARN` | Separate AWS JSON secret containing `token` and optionally `url`. Default: `NETBOX_SECRET`. Existing URL/token values take precedence over its fields. |
| `--netbox-filter KEY=VALUE` | API filter, repeatable. Any CLI filters replace the entire `NETBOX_FILTERS` environment list. Repeated site/platform values are alternatives; repeated tags require all. |
| `--netbox-autofilter` | Restrict NetBox candidates to this poller's device/site/region tags or site-scoped prefixes. Default: `NETBOX_AUTOFILTER=false`. Requires a poller identity. |
| `--no-netbox-autofilter` | Disable poller ownership filtering even if enabled in the environment. |
| `--poller NAME` | Same identity as SNMP inventory's `[poller] name`. Default: `NETOPS_POLLER`. Accepts a bare name or `poller-NAME` tag; does not enable filtering by itself. |
| `--netbox-source-tag TAG` | Interface tag identifying an NTP/syslog source, such as `ntp-source`; repeatable. Overrides the source-tag set from `netbox.source_tags`; default tags are `ntp-source` and `syslog-source`. |

Autofilter applies to configuration features, `discover` and checks when their
inventory is NetBox. Its environment setting is ignored for CSV/direct-IP and
rollback runs; explicitly enabling it there is an error. CLI enable/disable and
poller flags override their environment settings. Read the [poller ownership
rules and examples](README.md#limit-netbox-inventory-to-this-poller) before enabling it.
Platform filtering still uses `--netbox-filter platform=SLUG`, independently of
ownership. Ownership selects devices; syslog policy tags still select actions.

The NetBox token is set with `NETBOX_TOKEN` or the NetBox secret; there is no
`--netbox-token` flag. For syslog, `--syslog-source-tag` selects that feature's
tag specifically. Interface tags select source interfaces on Cisco/Arista;
device tags select logging policies. They are different objects and purposes.

### Device authentication

| Flag | Meaning and default |
| --- | --- |
| `-u`, `--username USER` | Login identity for connecting to devices. Environment fallback: `NET_USER`. |
| `--password VALUE` | Device login password. Environment fallback: `NET_PASS`; `.env` or AWS avoids exposing it in the process command line. |
| `--secret VALUE` | SSH enable-mode secret. Environment fallback: `NET_ENABLE`. This is a value, not an AWS secret name. |
| `--key-file FILE` | SSH private key. Default: `NET_KEY_FILE`. Not supported for F5 REST authentication. |
| `--port PORT` | SSH port. Default: `NET_PORT`, then 22; CSV per-device port can override it. Use `--f5-port` for HTTPS. |
| `--conn-timeout SECONDS` | SSH TCP connection timeout. Default: `NET_CONN_TIMEOUT`, then 10 seconds. Use `--f5-timeout` for F5 HTTP requests. |

Resolve login fields independently: command-line value, selected AWS device
secret, shell/`.env` value, then an interactive password prompt if available.
CSV username/password fields override the resulting fleet credentials for that
row. F5 REST requires a username and password.

### AWS Secrets Manager

| Flag | Meaning and default |
| --- | --- |
| `--aws-secret NAME_OR_ARN` | Device login JSON secret. Enables that credential source. Default: `NET_AWS_SECRET`. |
| `--aws-region REGION` | Region for secret lookups. Default: `NET_AWS_REGION`, then `AWS_REGION`, otherwise the AWS SDK's region resolution. |
| `--aws-username-key KEY` | Top-level JSON key holding the device username. Default: `NET_AWS_USERNAME_KEY`, then `username`. |
| `--aws-password-key KEY` | Top-level JSON key holding the device password. Default: `NET_AWS_PASSWORD_KEY`, then `password`. |
| `--aws-enable-key KEY` | Top-level JSON key holding an optional enable secret. Default: `NET_AWS_ENABLE_KEY`, then `enable_secret`. |

These key flags contain **field names**, not credentials. They affect the
device login secret only. NTP, SNMP, managed local accounts, NetBox and
ServiceNow use their own secret flags and JSON shapes. AWS authenticates through
the normal SDK credential chain (for example, an instance role or AWS profile).

### F5 HTTPS

Accepted by `waf`, `syslog`, `snmp` and `banner`:

| Flag | Meaning and default |
| --- | --- |
| `--f5-port PORT` | Management HTTPS port. Default: `NETOPS_F5_PORT`, then 443. Range 1–65535. |
| `--f5-timeout SECONDS` | REST request timeout; must be positive. Default: `NETOPS_F5_TIMEOUT`, then 30 seconds. Saves allow at least 120 seconds. |
| `--f5-insecure` | Disable certificate verification. This is already the default; overrides `NETOPS_F5_VERIFY_TLS=true`. |
| `--f5-verify-tls` | Enable certificate verification, overriding `NETOPS_F5_VERIFY_TLS=false`. Mutually exclusive with `--f5-insecure`. |
| `--f5-login-provider NAME` | Authentication provider. Default: `NETOPS_F5_LOGIN_PROVIDER`, then `tmos`. |

NetBox TLS is separate: `NETBOX_VERIFY_TLS` in `.env`, or
`netbox.verify_tls` in standards, defaults to false. There is no NetBox TLS CLI
flag. Both TLS environment settings accept `true` or `false`. Cisco and Arista
use SSH, so these TLS flags do not control their connections. On Python 3.10+
the entrypoint loads system CAs through truststore when verification is enabled;
see [CA setup](README.md#waf-remote-syslog) for Ubuntu 24 and RHEL 9.

### Standards and environment files

| Flag | Meaning and default |
| --- | --- |
| `--standards FILE` | Desired-state YAML/JSON file. Default: `NETOPS_STANDARDS`, then a supported standards filename in the working directory, then the project directory. Also accepted by `discover` / `check-ntp`. |
| `--no-standards` | Ignore the standards file. Features still require their desired data from flags; file-only features may fail. Also accepted by `discover` / `check-ntp`, although hidden in their help. |
| `--env-file FILE` | Use a specific `.env`. Default search: working directory, then project directory. |
| `--no-env-file` | Do not read `.env`; existing shell variables still apply. |

Supported standard filenames are `standards.yaml`, `standards.yml`, and
`standards.json`. A real run does not silently use `standards.yaml.example`.
Shell environment values win over `.env`; CLI overrides are described per flag.
Do not assume every flag has an environment equivalent.

### Execution modes

| Flag | Meaning and default |
| --- | --- |
| `--add` | Default reconciliation mode: ensure desired settings, retain extras where the feature permits. Updating scalars or enforcing absence can still change/remove configuration. |
| `--replace` | Converge the feature's managed scope to the desired state, including removal of extras. It does not replace the entire device configuration. |
| `--apply` | Perform the planned changes. Default: absent/dry run. |
| `-y`, `--yes` | Skip interactive apply confirmation. Does not enable apply. Noninteractive runs already treat `--apply` as authorization. |
| `--no-save` | Do not save changed running configuration. Default: save after successful verification, when the platform supports saving. |
| `--no-verify` | Skip configuration readback after applying. Default: verify. This does **not** disable TLS verification. |

`--add` and `--replace` are mutually exclusive. Logging has additional policy
rules documented above. Device dry runs can still produce local logs/reports;
`--open-change` explicitly creates a ServiceNow record. A failed verification
prevents saving. F5 REST changes may be partial when an operation fails.

### Output and reports

| Flag | Meaning and default |
| --- | --- |
| `-w`, `--workers COUNT` | Maximum parallel device workers. Default: 10. |
| `-v`, `--verbose` | Include current configuration and device output. Default: concise output. |
| `--log-file FILE` | Detailed errors/tracebacks. Default: `NETOPS_LOG_FILE`, then `netops-debug.log`. |
| `--no-log-file` | Disable the debug log file. |
| `--debug` | Print tracebacks and include SSH transcripts in the log. Secrets are scrubbed. |
| `--report FILE` | Archive JSON at this exact path (overwrites). All commands; default is a unique file in the report directory. |
| `--report-dir DIR` | Automatic archive directory. Default: `NETOPS_REPORT_DIR`, then `<project>/reports`. All commands. |
| `--fail-on-diff` | Return exit code 2 when a configuration feature has pending changes. Without it, an ordinary successful dry run can return 0 even when it previews changes. |

Connection/output flags through `--debug` also apply to discovery, checks and
rollback. `--report` and `--report-dir` apply to every command, including selftest.
`--fail-on-diff` applies only to configuration features. Reports distinguish execution
success, initial compliance, pending changes, verification and save results;
an exit code of 0 alone does not mean the initial configuration matched.

Every run writes JSON automatically, including dry runs and failures. Each device
records original feature configuration, implementation and backout steps, resulting
state, effective action, and whether its action came from NetBox. See
[JSON run archives](RUN_ARCHIVES.md) for the schema and restoration guidance.

### Rollback journals

| Flag | Meaning and default |
| --- | --- |
| `--rollback-dir DIR` | Where configuration features write reversal journals. Default: `NETOPS_ROLLBACK_DIR`, then `<project>/.rollback`. Also controls rollback's search directory. |
| `--no-rollback-file` | Do not write a reversal journal for this run. Does not suppress the automatic JSON archive. Configuration features only. |

Journals record supported reversals; they cannot recover unreadable previous
passwords. F5 REST features describe manual rollback in their reports instead
of generating executable SSH reversal commands.

### Platform cache

| Flag | Meaning and default |
| --- | --- |
| `--platform-cache FILE` | Cache path. Default: `NETOPS_PLATFORM_CACHE`, then `<project>/.platform-cache.json`. |
| `--platform-cache-ttl HOURS` | Cached-platform lifetime. Default: `NETOPS_PLATFORM_CACHE_TTL`, then 24 hours. |
| `--no-platform-cache` | Disable reading/writing the cache. Devices missing an explicit platform need detection again. |

Explicit platform data wins over cached detection. Cache flags apply to
configuration features, `discover` and checks; rollback uses journal platforms.

### ServiceNow change control

Configuration features only:

| Flag | Meaning and default |
| --- | --- |
| `--open-change` | Create a Normal change from the dry-run plan and attach the report. Cannot be combined with `--apply` or `--change`. It does write to ServiceNow. |
| `--change NUMBER_OR_SYS_ID` | Implement under an existing approved change, then add results and close it when appropriate. Requires `--apply`. The tool does not approve changes. |
| `--snow-instance NAME_OR_URL` | Instance override. Fallback: `change.instance`, then `SNOW_INSTANCE`. |
| `--snow-secret NAME_OR_ARN` | Separate AWS secret with ServiceNow credentials. Default: `SNOW_SECRET`. |

Environment credentials are `SNOW_USER` / `SNOW_PASS` or `SNOW_CLIENT_ID` /
`SNOW_CLIENT_SECRET`; request timeout is `SNOW_TIMEOUT` (30 seconds by default).
See the [ServiceNow guide](README.md#servicenow-change-records) for credential
formats, approval states and change fields in standards.

## Environment-only settings and precedence

These have no same-named CLI flag. The tables above list other environment
equivalents next to their corresponding flags.

| Variable | Purpose |
| --- | --- |
| `NETOPS_INVENTORY` | Default inventory source, `csv` or `netbox`; explicit source flags win. |
| `NETBOX_TOKEN` | NetBox API token; takes precedence over the NetBox secret's token. |
| `NETBOX_FILTERS` | Space-separated `KEY=VALUE` API filters; replaced by any `--netbox-filter` arguments. |
| `NETBOX_VERIFY_TLS` | NetBox TLS verification; overrides standards, default false. |
| `NETOPS_F5_VERIFY_TLS` | F5 TLS verification; overridden by the explicit F5 TLS flags, default false. |
| `NETOPS_SYSLOG_POLICY` | Logging policy default for `syslog` / `waf`; explicit policy/mode flags win. |
| `NETOPS_F5_POLICY` | Legacy logging policy fallback used only if `NETOPS_SYSLOG_POLICY` is unset. |
| `NETOPS_NTP_KEY_<ID>` | NTP key material. |
| `NETOPS_SNMP_AUTH_<USER>`, `NETOPS_SNMP_PRIV_<USER>` | SNMPv3 auth/privacy passphrases. |
| `NETOPS_PW_<USER>` | Password/hash for a managed local account. |
| `NETOPS_TEMPLATES` | Override the template directory. |
| `REQUESTS_CA_BUNDLE` | Explicit CA bundle for Requests HTTPS clients when verification is enabled. |
| `AWS_CA_BUNDLE` | CA bundle for AWS SDK HTTPS requests. |
| `SNOW_USER`, `SNOW_PASS`, `SNOW_CLIENT_ID`, `SNOW_CLIENT_SECRET`, `SNOW_TIMEOUT` | ServiceNow authentication and timeout. |

Precedence differs by setting; there is no universal “environment always wins”
rule. Shell beats `.env`, device login flags beat AWS login fields, AWS login
fields beat environment credentials, while NetBox's configured URL/token take
precedence over its AWS secret. NetBox tagged source interfaces override a
fleet-wide `--source`. Defaults in this document assume no overrides.

## Recipes

Preview a single device identified by its NetBox name:

```bash
./configure.py snmp --netbox --limit us-uat-us-east4-bigip1 --replace --report snmp-preview.json
```

Use different logging actions for devices in one run:

```bash
./configure.py syslog --netbox --policy netbox --apply --yes --report syslog-results.json
```

Select a site and platform at the NetBox API, then narrow by device name:

```bash
./configure.py banner --netbox --netbox-filter site=atl --netbox-filter platform=ios-xe --limit core-sw1
```

Use custom JSON keys in an AWS device-login secret:

```bash
./configure.py waf --netbox --aws-secret prod/network/f5 --aws-region us-east-1 \
  --aws-username-key login_name --aws-password-key login_password --policy audit
```

Preview an Arista device by direct IP:

```bash
./configure.py syslog --ip 192.0.2.20 --platform arista_eos --policy manage
```

Enable certificate verification separately for NetBox and F5 in `.env`:

```dotenv
NETBOX_VERIFY_TLS=true
NETOPS_F5_VERIFY_TLS=true
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Run completed successfully; dry-run drift can still be present unless fail-on-diff was requested. |
| 1 | Device/operation failure, failed verification or other runtime failure. |
| 2 | Drift/attention (`--fail-on-diff`, an unresolved advisory, or an NTP check attention result). Argparse also uses 2 for invalid command syntax, so read the error message. |
| 3 | Settings, credentials, inventory or approval problem handled as a usage error. |
| 130 | Interrupted by the operator. |

For programmatic reporting, inspect each device's report fields along with
the exit code. Utility commands do not all produce JSON reports.

### `upgrade-poll`: scheduled work from NetBox

```sh
./configure.py upgrade-poll --poller checkmk-us --workers 3          # scheduled audits only
./configure.py upgrade-poll --poller checkmk-us --workers 3 --apply  # also staging/upgrades
```

Checks in once, claims a bounded batch, runs it and exits. Intended for cron each
minute; overlapping ticks exit quietly. Targets, operation, and profile come
from the NetBox schedule. Shared environment/AWS credentials and upgrade timeout
flags apply. `--queue-state-dir` selects persistent private delivery/lock state;
`--report-dir` holds one archive per job. See [SCHEDULED_UPGRADES.md](SCHEDULED_UPGRADES.md)
for the NetBox form, deployment, permissions, API, and recovery rules.
