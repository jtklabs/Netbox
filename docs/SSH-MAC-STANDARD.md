# SSH MAC checks with raw standards

The dedicated `ssh-mac` CLI feature, `ssh_mac` YAML section, feature picker entry,
and `create_ssh_mac_standard` seed command have been retired in worker 0.2.19.
Use [raw command standards](RAW-COMMAND-STANDARDS.md) for SSH MAC checks and optional
remediation. Existing compliance results and revisions are retained.

Create a named raw standard scoped to the intended platforms. For an IOS check:

```yaml
raw_commands:
  enabled: true
  platforms:
    cisco_ios:
      show: show running-config
      contains:
        - regex: '^ip ssh server algorithm mac(?:[ \t]+hmac-sha2-(?:256|512))+[ \t]*\r?$'
        - regex: '^ip ssh client algorithm mac(?:[ \t]+hmac-sha2-(?:256|512))+[ \t]*\r?$'
```

This example requires both configuration lines to contain only SHA-256/SHA-512
MACs. Adjust the permitted list and direction to your policy and device output.
No configuration commands are present, so it only reports pass/fail. To remediate,
add the exact desired `commands` bundle and enable the standard's remediation
permissions as described in the raw command documentation.

Replace `ssh_mac` in saved profiles and schedules with `raw_commands` and select
the new standard explicitly. Alternatively, replace the YAML in existing SSH MAC
standards with raw definitions to keep their identity and result history. End-date them after updating their definitions if
they should no longer appear in the current compliance grid. Remove the old
`ssh_mac` section from local standards files. Old profiles and queued jobs are
rejected rather than silently skipping the retired feature; recreate pending
jobs with the raw standard. Previously pinned revisions are not rewritten.
