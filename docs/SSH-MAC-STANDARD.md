# SSH MAC integrity standard

The **SSH MAC integrity** standard audits SSH message authentication and can
replace the configured MAC allow-list. It does not manage encryption ciphers,
key exchange, host keys or SSH access rules.

Define the complete allowed MAC list in the standards YAML. Each platform can
have its own list; values are used for planning, command rendering and read-back
verification. The example below retains SHA-256 and SHA-512 on IOS/IOS-XE, EOS
and Junos, and selects SHA-256 explicitly on ASA. It excludes SHA-1 MACs.

Lists are compared as sets: order alone does not cause drift, but an extra MAC
or a missing allowed MAC does. This is an allow-list, not an automatic weak-MAC
classifier: include every algorithm you intend to allow. Software determines
which algorithms are supported. These are configuration checks, not handshake
or reachability tests; check software support and peer compatibility before
applying a change to SSH management access.

## Create in NetBox

After deploying the updated Config Compliance and Discovery plugins and
worker, run this in the NetBox environment:

```sh
python manage.py create_ssh_mac_standard --dry-run
python manage.py create_ssh_mac_standard
```

The command creates one versioned YAML standard, initially **audit-only**, and
scopes it to recognized IOS/IOS-XE, EOS, ASA and Junos platforms. It refuses to
create an unrestricted standard if none are found. Use repeatable
`--platform <name-or-slug>` arguments for custom platform names, and
`--ios-scope client` for a Cisco client-only policy. Re-running the
command preserves an existing standard of the same name, including edits and
scope changes.

The equivalent **Feature settings (YAML)** definition is:

```yaml
ssh_mac:
  ios_scope: both
  platforms:
    cisco_ios:
      macs: [hmac-sha2-256, hmac-sha2-512]
    arista_eos:
      macs: [hmac-sha2-256, hmac-sha2-512]
    cisco_asa:
      macs: [hmac-sha2-256]
    juniper_junos:
      macs: [hmac-sha2-256, hmac-sha2-512]
```

Choose **SSH MAC integrity** in a standards audit or saved job profile. Jobs
pin the standard revision and publish results through the existing compliance
report. To enable remediation, turn on **Can be remediated automatically** and
**Allow enforce (removals)** on this standard and select **Replace** mode.
Add mode reports drift and sends no MAC changes: a positive configuration
command can still remove algorithms from an allow-list.

## Command line

```sh
# Read-only comparison using a CSV inventory and the standard above.
python configure.py ssh-mac --csv devices.csv --standards standards.yaml --replace --fail-on-diff

# Apply the reviewed policy, then read it back and save on successful verification.
python configure.py ssh-mac --csv devices.csv --standards standards.yaml --replace --apply
```

Run from `scripts/nornir-netops`. The same definition works in a standalone
`standards.yaml` and NetBox's **Feature settings (YAML)** editor. The worker
loads it through `--standards`; queued jobs receive their pinned NetBox revision.

## Settings and precedence

- `macs`: optional shared list for IOS/IOS-XE, EOS and Junos.
- `ios_scope`: `both`, `client` or `server`; defaults to `both`.
- `platforms`: optional overrides keyed by `cisco_ios`, `arista_eos`,
  `cisco_asa` or `juniper_junos`. IOS-XE uses `cisco_ios`.
- Each platform accepts `macs`. IOS also accepts `ios_scope`.
- ASA accepts either `macs` for an explicit custom list or `asa_integrity`
  for a named preset (`all`, `low`, `medium`, `high`, `fips`), never both
  in the same platform override. Global `asa_integrity` remains supported.

For example, a shared list with an ASA override is:

```yaml
ssh_mac:
  macs: [hmac-sha2-256, hmac-sha2-512]
  ios_scope: both
  platforms:
    cisco_asa:
      macs: [hmac-sha2-256]
```

Platform values override shared values. `--ios-scope` overrides both YAML
scope settings. Shared `macs` does not apply to ASA: it uses its explicit
platform list/preset or the legacy `asa_integrity` default of `high`.
A missing list on IOS, EOS or Junos retains the legacy `[hmac-sha2-512]`
default. Existing standards are never rewritten by the seeder; edit their YAML
to adopt the example. New seeds use the explicit per-platform example above.

Algorithm lists must be nonempty, unique lists of `hmac-…` or `umac-…` names.
ETM names such as `hmac-sha2-256-etm@openssh.com` are accepted. Invalid types,
unknown keys/platforms and command fragments are rejected before connecting.
This validates syntax, not device support or algorithm strength; the policy
can explicitly name a legacy algorithm if an exception requires it. A device
that rejects a configured algorithm fails the job rather than silently dropping it.

ASA custom lists render as `ssh cipher integrity custom` with colon-separated
algorithm names. Named presets remain available for existing standards, but
their membership depends on the release; `high` is not a guarantee of no SHA-1.
Use an explicit ASA `macs` list for that requirement. This feature continues to
manage MAC integrity only; encryption ciphers and key exchange are unchanged.

IOS, EOS and ASA use the existing command journal and read-back verification.
Their rollback restores the previous MAC setting without deleting the SSH
management block. Junos edits only the `macs` leaf-list in a private candidate,
runs a commit check, commits confirmed for five minutes, reads the configuration
back and immediately confirms a matching result permanently. Failed verification
leaves the temporary commit unconfirmed so Junos can roll it back. Inherited, inactive or protected
Junos SSH configuration requires manual remediation. Junos also refuses writes
when a pending commit or candidate changes are detected, when SSH is not
explicitly configured, or when saving/verification is disabled. Restoring an
already confirmed Junos change requires a reviewed manual change.

## Command references

- [Cisco IOS SSH MAC algorithms](https://www.cisco.com/c/en/us/td/docs/ios-xml/ios/security/d1/sec-d1-cr-book/sec-cr-i3.html)
- [Arista SSH management commands](https://www.arista.com/en/um-eos/eos-session-management-commands)
- [Cisco ASA SSH integrity command](https://www.cisco.com/c/en/us/td/docs/security/asa/asa-cli-reference/S/asa-command-ref-S/so-st-commands.html)
- [Junos SSH service and MAC settings](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/ssh-edit-system.html)
