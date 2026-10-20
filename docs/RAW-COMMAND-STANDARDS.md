# Raw command standards

The `raw_commands` section runs show-output pass/fail checks with optional literal configuration commands. It works in `standards.yaml` and in a NetBox configuration standard's YAML definition. Select **Raw commands** in the remediation profile, or use the `raw-commands` CLI feature.

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

## Read-only operational checks

Omit `commands`, or set `commands: []`, to run only the selected show command.
The standard passes only when every `expect`/`contains` check matches and every
`reject` check is absent. A failure is recorded as non-compliant, with the failed
checks in the report. No configuration, save or commit is sent, including when
run in replace mode with `--apply`. CLI failures return 2; connection errors,
empty output and rejected commands return 1; a passing check returns 0.

For IOS/IOS-XE, require a selected NTP peer (`*`) and fail if `INIT` appears:

```yaml
raw_commands:
  enabled: true
  platforms:
    cisco_ios:
      show: show ntp associations
      contains:
        - regex: '^\s*\*~?\S+\s+'
      reject:
        - regex: '(?i)\bINIT\b'
```

The `reject` check searches the full output, so any association in INIT fails,
even if another peer is selected. The required peer pattern excludes the `* sys.peer`
legend by requiring an address immediately after the selection flags. To test
only absence of INIT, omit `contains`.
Absence alone does not establish synchronization. For a direct clock-status
check, create a second named raw standard using:

```yaml
raw_commands:
  enabled: true
  platforms:
    cisco_ios:
      show: show ntp status
      contains:
        - regex: '(?i)\bClock is synchronized\b'
      reject:
        - regex: '(?i)\bunsynchronized\b'
```

Select both named standards in an audit profile to check associations and clock
status. Use **Feature settings (YAML)**, clear **Can be remediated automatically**,
and run **Audit only**. A NetBox standard still stores the check definition;
no device configuration entry or remediation command is needed.

`reject` accepts literal strings or `{regex: 'pattern'}` just like `contains`.
A forbidden regex match is reported as `forbidden regex: <pattern>`. Literal
strings are case-sensitive; use `(?i)` in a regex for case-insensitive matching.
An empty show response remains an error, even for absence-only checks; run the
full show command rather than piping away all healthy output.

Deploy the Compliance and Discovery plugins with worker **0.2.19** or newer.
No database migration is required. Older workers reject omitted/empty commands
and regex entries in `reject`.

## Selection and execution

- Supported platform keys: `cisco_ios`, `arista_eos`, `cisco_asa`, `cisco_nxos`, `juniper_junos`, `infoblox_nios`. Inventory aliases are normalized; YAML uses these canonical keys.
- For each device, the nearest matching region in its NetBox site ancestry wins. If none matches, the platform default wins. CSV inventories use the `region` column. Region keys are lowercase NetBox slugs.
- A region replaces the **whole bundle**, including its checks. Commands are never appended to the platform default. A platform may contain only regional bundles; an unmatched device then fails with an explicit error.
- `enabled: true` is mandatory for a run. `false` blocks the feature and reports an error rather than a compliant result. The shipped example is disabled.
- Commands run once, in the exact list order, only when checks report drift. No Jinja substitution, automatic removal commands, or automatic rollback commands are generated. Repeated commands and submode `exit` commands are preserved.
- Supply configuration statements, one YAML string per command. For Cisco and Arista, the worker enters configuration mode and saves after verification. Junos uses the transaction described below; Infoblox sends commands directly. Omit `configure terminal`, `end`, save and commit commands. Interactive prompts and multiline banner bodies are outside this feature. Omit `commands` for read-only operational checks.
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

`expect` matches complete lines in the selected show output, ignoring leading and trailing whitespace. `contains` accepts literal strings and explicit `regex` items; `reject` forbids literal strings or explicit `regex` matches anywhere in the output. Plain strings remain literal and case-sensitive, including strings containing regex punctuation. `expect` does not accept regex items. Choose checks from the device's actual displayed configuration: spelling and algorithm order may differ from the input commands. All expected lines, required fragments and regex matches must exist, and every rejected fragment or regex match must be absent. A check covers only what it states; it does not infer encryption or MAC security from a command.

By default, the worker reads `show running-config` on IOS, EOS, ASA and NX-OS, and `show configuration | display set` on Junos. Set `show` within a platform or regional bundle to select a different command. Checks are against that command's output; indentation is not a hierarchy selector. Choose sufficiently specific lines, and use the typed feature where its parser is needed to distinguish sections. Infoblox requires an explicit `show` command in each bundle. An empty or rejected read fails instead of being considered compliant. Reports retain failed-check evidence rather than the full configuration.

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

A regex item supports only the `regex` key with a nonempty, single-line string up to 2,048 characters. Each `contains` or `reject` list supports up to 256 items. Invalid regex syntax is rejected when NetBox validates the definition and when the worker loads the standard, before connecting to devices. Use specific patterns; a broad expression such as `.*` can pass without proving the intended setting exists.

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

Audits use the same checks as post-change verification. If checks already pass, the bundle is not sent. Failed verification fails the job and prevents a separate save or commit confirmation. Infoblox has no separate save step; commands already accepted may remain active. `--no-verify` is refused for an apply. Raw statements are visible in standards, revisions and reports; do not embed credentials in them.

Junos accepts `set`, `delete`, `activate`, `deactivate`, `insert` and `rename` statements. It uses a private candidate, checks for pending changes and commits, runs `commit check`, then `commit confirmed 5`, verifies the active configuration and confirms permanently only after the checks pass. An unconfirmed failed change is left to roll back. Junos requires saving and verification. There is no generated rollback after a permanent commit.

## Infoblox NIOS

Use `infoblox_nios` as the YAML platform key. NetBox's `infoblox-nios` platform slug is supported; CSV and direct-IP inventory also accept `infoblox`, `infoblox-nios`, and `nios`. Configure the platform explicitly rather than relying on autodetection.

Every default or regional Infoblox bundle requires its own `show` command. For example, this standard disables TCP timestamps and verifies their displayed state:

```yaml
raw_commands:
  enabled: true
  platforms:
    infoblox_nios:
      commands:
        - set tcp_timestamps disable
      show: show tcp_timestamps
      contains:
        - regex: '^TCP timestamps are disabled\.\r?$'
```

The example uses Infoblox's documented [set tcp_timestamps](https://infoblox-docs.atlassian.net/wiki/spaces/nios86/pages/35984513/set%2Btcp_timestamps) and [show tcp_timestamps](https://infoblox-docs.atlassian.net/wiki/spaces/nios90/pages/280887741/show%2Btcp_timestamps) commands. Check command availability and output on the target NIOS release when writing other standards.

The worker uses the generic SSH driver and sends each noninteractive `set` command individually, in order, at the NIOS prompt. It does not send `enable`, `configure terminal`, `write memory`, or `commit`. A rejected command or timeout stops the bundle. Interactive questions, confirmation responses, command chaining, and commands that interrupt the SSH connection are not supported.

Read-only audits send only the selected `show` command. Applies require replace mode and verification, and run the same literal or regex checks afterward. Regional overrides and selection of multiple named raw standards work as on the other platforms. A regional bundle replaces the default completely, so it must include its own `show` and checks, plus `commands` if remediation is wanted.

NIOS commands may take effect before verification; failure does not undo earlier commands. Reports use `verified` for the readback result and leave `saved` and `save_command` null because no separate persistence step is performed. `--no-save` does not make NIOS commands temporary. Automatic rollback is unavailable.

Deploy the updated Compliance plugin and netops worker **0.2.18** or newer before using Infoblox raw standards. No database migration is required. Validation rejects the new platform on older workers.

## Run

```sh
# Read-only preview; a nonzero diff status indicates drift.
python configure.py raw-commands --standards standards.yaml --csv devices.csv --replace --fail-on-diff

# Apply the selected bundle and verify before saving.
python configure.py raw-commands --standards standards.yaml --csv devices.csv --replace --apply --yes
```

For NetBox, create a standard with check type **Feature settings (YAML)**, paste the section above, scope it to the intended devices and platforms, and select **Raw commands** in the profile. Use replace mode for remediation. Queued jobs pin the complete YAML revision; subsequent edits affect later jobs.

SSH MAC checks now use raw standards; the dedicated `ssh_mac` feature is retired. See [SSH MAC migration](SSH-MAC-STANDARD.md) for replacing existing profiles and preserving historical results. Avoid selecting competing standards for the same setting in one job.

## Select raw standards in a job profile

Create separate NetBox YAML standards for separate raw bundles, such as **SSH integrity** and **Disable HTTP**. They can apply to the same devices.

1. Open **Job Profiles** and create or edit a **Remediation** profile.
2. Select **Raw commands** in the feature list.
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
