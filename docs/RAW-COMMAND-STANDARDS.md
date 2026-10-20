# Raw command standards

The `raw_commands` section provides an explicit escape hatch for literal configuration commands. It works in `standards.yaml` and in a NetBox configuration standard's YAML definition. Select **Raw configuration commands** in the remediation profile, or use the `raw-commands` CLI feature.

```yaml
raw_commands:
  enabled: true
  platforms:
    cisco_ios:
      commands:
        - ip ssh server algorithm mac hmac-sha2-256 hmac-sha2-512
      expect:
        - ip ssh server algorithm mac hmac-sha2-256 hmac-sha2-512
      regions:
        us-east:
          commands:
            - ip ssh server algorithm mac hmac-sha2-512
          expect:
            - ip ssh server algorithm mac hmac-sha2-512
    arista_eos:
      commands:
        - management ssh
        - mac hmac-sha2-256 hmac-sha2-512
        - exit
      expect:
        - mac hmac-sha2-256 hmac-sha2-512
```

## Selection and execution

- Supported platform keys: `cisco_ios`, `arista_eos`, `cisco_asa`, `cisco_nxos`, `juniper_junos`. Inventory aliases are normalized; YAML uses these canonical keys.
- For each device, the nearest matching region in its NetBox site ancestry wins. If none matches, the platform default wins. CSV inventories use the `region` column. Region keys are lowercase NetBox slugs.
- A region replaces the **whole bundle**, including its checks. Commands are never appended to the platform default. A platform may contain only regional bundles; an unmatched device then fails with an explicit error.
- `enabled: true` is mandatory for a run. `false` blocks the feature and reports an error rather than a compliant result. The shipped example is disabled.
- Commands run once, in the exact list order, only when checks report drift. No Jinja substitution, automatic removal commands, or automatic rollback commands are generated. Repeated commands and submode `exit` commands are preserved.
- Supply configuration statements, one YAML string per command. The worker enters configuration mode and saves after verification. Omit `configure terminal`, `end`, save and commit commands. Interactive prompts, multiline banner bodies and operational command jobs are outside this feature.
- Execution requires replace mode. Add mode audits and reports drift without executing the bundle. Existing NetBox permissions, standard scope, `auto_remediable`, `allow_enforce`, job approval and revision snapshots still apply.

## Compliance and verification

Each default or regional bundle needs `expect`, `reject`, or both:

```yaml
commands:
  - no service pad
expect:
  - no service pad
reject:
  - hmac-sha1
```

`expect` matches complete lines in the running configuration, ignoring leading and trailing whitespace. `reject` matches literal, case-sensitive text anywhere in that output. Neither uses regular expressions. Choose checks from the device's actual displayed configuration: spelling and algorithm order may differ from the input commands. All expected lines must exist and every rejected fragment must be absent. A check covers only what it states; it does not infer encryption or MAC security from a command.

The worker reads `show running-config` on IOS, EOS, ASA and NX-OS, and `show configuration | display set` on Junos. Checks are against this full output; indentation is not a hierarchy selector. Choose sufficiently specific lines, and use the typed feature where its parser is needed to distinguish sections. An empty or rejected read fails instead of being considered compliant. Reports retain failed-check evidence rather than the full configuration.

Audits use the same checks as post-change verification. If checks already pass, the bundle is not sent. Failed verification fails the job and prevents saving. `--no-verify` is refused for an apply. Raw statements are visible in standards, revisions and reports; do not embed credentials in them.

Junos accepts `set`, `delete`, `activate`, `deactivate`, `insert` and `rename` statements. It uses a private candidate, checks for pending changes and commits, runs `commit check`, then `commit confirmed 5`, verifies the active configuration and confirms permanently only after the checks pass. An unconfirmed failed change is left to roll back. Junos requires saving and verification. There is no generated rollback after a permanent commit.

## Run

```sh
# Read-only preview; a nonzero diff status indicates drift.
python configure.py raw-commands --standards standards.yaml --csv devices.csv --replace --fail-on-diff

# Apply the selected bundle and verify before saving.
python configure.py raw-commands --standards standards.yaml --csv devices.csv --replace --apply --yes
```

For NetBox, create a standard with check type **Feature settings (YAML)**, paste the section above, scope it to the intended devices and platforms, and select **Raw configuration commands** in the profile. Use replace mode for remediation. Queued jobs pin the complete YAML revision; subsequent edits affect later jobs.

This is independent of `ssh_mac`, NTP and other typed features. Avoid selecting competing standards for the same setting in one job. Raw commands do not change the typed SSH MAC policy or its parser.
