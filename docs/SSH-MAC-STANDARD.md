# SSH MAC integrity standard

The **SSH MAC integrity** standard audits SSH message authentication and can
replace the configured MAC allow-list. It does not manage encryption ciphers,
key exchange, host keys or SSH access rules.

| Platform | Required configuration |
| --- | --- |
| Cisco IOS / IOS-XE | `ip ssh client algorithm mac hmac-sha2-512` and `ip ssh server algorithm mac hmac-sha2-512` |
| Arista EOS | `management ssh` → `mac hmac-sha2-512` |
| Cisco ASA | `ssh cipher integrity high` |
| Juniper Junos | `set system services ssh macs hmac-sha2-512` |

IOS defaults to both client and server. Use `ios_scope: client` to manage
only outbound SSH.
ASA's `high` preset is release dependent and is not equivalent to SHA-512.
The ASA check verifies the named preset, not the negotiated algorithms.
On the other platforms, SHA-512 alongside another MAC is drift: the entire
configured list must match. These are configuration checks, not handshake
or reachability tests. Check software support and peer compatibility before
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
  macs: [hmac-sha2-512]
  ios_scope: both
  asa_integrity: high
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

Run from `scripts/nornir-netops`. `--ios-scope client|server|both` overrides
the YAML setting for IOS. The initial policy supports only the values shown
above; unsupported values fail validation instead of becoming CLI fragments.

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
- [Cisco ASA 9.12 SSH integrity defaults](https://www.cisco.com/c/en/us/td/docs/security/asa/asa912/release/notes/asarn912.html)
- [Junos SSH service and MAC settings](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/ssh-edit-system.html)
