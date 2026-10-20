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

Each default or regional bundle needs at least one `expect`, `contains`, or `reject` check:

```yaml
commands:
  - no service pad
expect:
  - no service pad
reject:
  - hmac-sha1
```

`expect` matches complete lines in the selected show output, ignoring leading and trailing whitespace. `contains` accepts literal strings and explicit `regex` items; `reject` forbids literal text anywhere in the output. Plain strings remain literal and case-sensitive, including strings containing regex punctuation. `expect` and `reject` do not accept regex items. Choose checks from the device's actual displayed configuration: spelling and algorithm order may differ from the input commands. All expected lines, required fragments and regex matches must exist, and every rejected fragment must be absent. A check covers only what it states; it does not infer encryption or MAC security from a command.

By default, the worker reads `show running-config` on IOS, EOS, ASA and NX-OS, and `show configuration | display set` on Junos. Set `show` within a platform or regional bundle to select a different command. Checks are against that command's output; indentation is not a hierarchy selector. Choose sufficiently specific lines, and use the typed feature where its parser is needed to distinguish sections. An empty or rejected read fails instead of being considered compliant. Reports retain failed-check evidence rather than the full configuration.

### Regex checks in contains

Use a `regex` mapping for any `contains` item that needs pattern matching. Literal and regex checks can be mixed in the same list, in platform defaults or regional bundles:

```yaml
raw_commands:
  enabled: true
  platforms:
    cisco_ios:
      commands:
        - ip ssh server algorithm mac hmac-sha2-256 hmac-sha2-512
      show: show running-config | include ^ip ssh server algorithm mac
      contains:
        - hmac-sha2-512
        - regex: '^\s*ip ssh server algorithm mac(?:[ \t]+hmac-sha2-(?:256|512))+[ \t]*\r?$'
      reject:
        - hmac-sha1
```

This pattern requires a complete server MAC configuration line containing only `hmac-sha2-256` and/or `hmac-sha2-512`. The separate literal item also requires `hmac-sha2-512` somewhere in the selected output. Checks describe the displayed output; they do not parse other SSH settings or infer their security.

Patterns use Python regular expression syntax and search the entire show response. Multiline mode is enabled: `^` and `$` anchor to individual lines. Matching is case-sensitive unless the pattern starts with `(?i)`. A dot does not match a newline unless `(?s)` is enabled. Output whitespace is preserved; include indentation or carriage returns in the pattern when needed. Single-quote YAML patterns so backslashes such as `\s` and `\b` reach the regex unchanged.

Every item must pass. Regex matching applies to read-only audits and the readback after remediation. A failed pattern is reported as `missing regex: <pattern>` in `failed_checks` and, after an apply, `missing_after`. Reports label the desired check as `contains regex: <pattern>`. Existing literal failures still use `missing text: <text>`.

A regex item supports only the `regex` key with a nonempty, single-line string up to 2,048 characters. Each `contains` list supports up to 256 items. Invalid regex syntax is rejected when NetBox validates the definition and when the worker loads the standard, before connecting to devices. Use specific patterns; a broad expression such as `.*` can pass without proving the intended setting exists.

Deploy the updated Compliance plugin and netops worker **0.2.17** or newer before using regex entries. No database migration is required. Existing plain-text standards retain their behavior; older workers reject regex items during validation.

### Audit a show command

```yaml
raw_commands:
  enabled: true
  platforms:
    cisco_ios:
      commands:
        - ip ssh server algorithm mac hmac-sha2-512
      show: show running-config | include ^ip ssh server algorithm mac
      contains:
        - hmac-sha2-512
      reject:
        - hmac-sha1
```

For example, if the show output is `ip ssh server algorithm mac hmac-sha2-512 hmac-sha1`, the required text passes but the forbidden text fails. A read-only audit records:

```json
{
  "compliant": false,
  "config_before": {
    "show_command": "show running-config | include ^ip ssh server algorithm mac",
    "failed_checks": ["forbidden text: hmac-sha1"]
  }
}
```

If `hmac-sha2-512` is missing, the report also lists `missing text: hmac-sha2-512`. A successful audit has `compliant: true` and an empty failed-check list. After remediation, `config_after` records the same command and its results; `verified` is true only if all checks pass. The report does not retain the raw show output.

`show` must be one operational command beginning with `show`. Read-only filters (`include`, `exclude`, `begin`, `section`, `count`, `match`, `except`, `find`, `display`, `no-more`, `trim`) are accepted; command chaining and output redirection are rejected. Commands and filters must be supported by the device's software. An empty response, including a filter with no results, reports an audit error. For absence-only checks, choose a show command with a nonempty response rather than filtering away all output.

Each regional bundle is complete: omitting `show` there uses the platform's built-in default, not another bundle's custom command. Existing YAML without `show` or `contains` keeps its original behavior. On Junos the selected show command runs in operational context, including after the confirmed commit; the concurrent-change guard still compares configuration separately.

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

## Select raw standards in a job profile

Create separate NetBox YAML standards for separate raw bundles, such as **SSH integrity** and **Disable HTTP**. They can apply to the same devices.

1. Open **Job Profiles** and create or edit a **Remediation** profile.
2. Select **Raw configuration commands** in the feature list.
3. Choose up to eight named standards in **Raw command standards**.
4. Save the profile and use it in an audit or remediation schedule. Audit-only scheduling does not send configuration commands.

Only the selected raw standards are included. Each retains its own platform/region bundle, `show` command, expectations, pinned revision and compliance result. They run separately in ascending standard ID order; the order of commands within each bundle remains unchanged. The same multi-select is available for custom audit schedules and configuration-job edits. On the device page, choosing one named standard in **Run standards now** still selects only that standard; choosing a profile uses that profile's selections.

Leaving the picker blank preserves automatic selection: exactly one visible, in-force raw standard must apply to each device. Multiple automatic matches block scheduling; a blank picker never means run everything. Unrelated NTP or other feature jobs ignore raw standards. Every selected standard must be visible to the scheduling user, active, in device scope, and permitted for the requested operation. A missing, deleted, inaccessible or out-of-scope selection fails rather than silently skipping or falling back.

The API profile accepts a list of standard IDs:

```yaml
features: [raw_commands]
mode: replace
raw_standard_ids: [42, 57]  # IDs of the selected NetBox standards
```

The list must contain one through eight distinct positive integers. Legacy `raw_standard_id: 42` plans remain supported, and the forms retain that representation for a single selection. Do not supply both keys. A profile may also list typed features such as `ntp`; those retain their own applicable standards.

Scheduling freezes each selected standard's current revision and YAML. The worker checks the selected IDs against the pinned snapshot. Apply retains the start-time revision check; if a selected standard changes after scheduling, recreate the job. Changes to an unselected standard do not add it to the job. Editing a saved profile affects future jobs; existing jobs retain their selections.

All selected checks run read-only before the start gate. Remediation applies only bundles with drift. After the apply pass, the worker audits all selected raw standards again, including those initially compliant, so a later bundle cannot silently undo an earlier check. Conflicting expectations produce noncompliance rather than an automatic clean pass. Bundles are not a transaction and do not have a generated rollback; avoid competing commands for the same setting. Progress and compliance evidence use `raw_commands:<standard ID>` for separate results in multi-selection jobs.

Deploy the updated Discovery and Compliance plugins together with netops worker **0.2.16** or newer before scheduling multiple selections. No database migration is required. Older workers reject plural-selection profiles before executing device commands. Existing single-selection jobs remain compatible.
