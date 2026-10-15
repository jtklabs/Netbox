# F5 rollback

The `rollback` command restores F5 system syslog, user-created WAF remote
logging destinations, SSH/web banners and SNMP settings using BIG-IP REST.
It uses the original values captured before the change, not today's standards.

## Preview and apply

```bash
# Explicit applied-run archive (a .rollback journal also works):
./configure.py rollback reports/ORIGINAL-RUN.json --limit bigip1
./configure.py rollback reports/ORIGINAL-RUN.json --limit bigip1 --apply

# Most recent journal in NETOPS_ROLLBACK_DIR or .rollback:
./configure.py rollback
./configure.py rollback --apply
```

Preview is the default. `--apply` prompts before changing devices; `--yes` skips
the prompt. `--no-save` leaves the restored running configuration unsaved.
F5 rollback always reads back the restored fields before saving. Already restored
readable settings are skipped; supplied secrets are reapplied because REST cannot
confirm their values. A failure prevents saving that device and returns failure.

Device keys, management addresses and platforms come from the source file.
`--limit` accepts exact keys from that file. Current CSV/NetBox inventory and
policy tags do not select the rollback action. Mixed Cisco/Arista/F5 archives
use SSH or REST as appropriate. Rollback does not update NetBox custom fields.

Login uses the existing credentials flags and environment settings, including
AWS secret name and username/password keys. F5 connection options also apply:
`--f5-port`, `--f5-timeout`, `--f5-login-provider`, `--f5-verify-tls` and
`--f5-insecure`. Device certificate verification is always disabled for self-signed
certificates. Legacy TLS flags and `NETOPS_F5_VERIFY_TLS` cannot enable it. See [CLI reference](CLI_REFERENCE.md).

## Previous SNMP credentials

Archives redact SNMP passphrases and community strings. Restoring a removed or
modified user/community therefore requires its **previous** secrets. Removing
a newly created user does not require that user's password. Missing secrets or
original readable settings block all REST configuration writes for that device.
A preview reports the missing fields and resource paths.

Supply a JSON file with device keys matching the source archive and resource
paths matching its restore steps:

```json
{
  "bigip1": {
    "/mgmt/tm/sys/snmp/users/~Common~old-user": {
      "authPassword": "previous-auth-passphrase",
      "privacyPassword": "previous-privacy-passphrase"
    },
    "/mgmt/tm/sys/snmp/communities/~Common~old-community": {
      "communityName": "previous-community-string"
    }
  }
}
```

```bash
./configure.py rollback reports/ORIGINAL-RUN.json --rollback-secrets /secure/previous-snmp.json
./configure.py rollback reports/ORIGINAL-RUN.json --rollback-secrets /secure/previous-snmp.json --apply
```

Alternatively store that same JSON object in AWS Secrets Manager and use
`--rollback-secret prod/network/previous-snmp`. This is separate from the
secret used for device login. `--aws-region` applies to both. Environment
alternatives are `NETOPS_ROLLBACK_SECRETS_FILE` and `NETOPS_ROLLBACK_SECRET`;
choose only one. An explicit source flag overrides these environment defaults.
Protect local secret files with restrictive permissions. Secret values are
redacted from previews, reports and debug logs.

Readback verifies readable SNMP fields. It cannot confirm previous passphrases
or perform an SNMP authentication test; the report records this limitation.
An older archive missing original readable settings cannot be fully restored
by supplying secrets alone.

## Archives, partial failures and scope

New F5 applies write REST journals by default. `--no-rollback-file` disables the
journal, but the automatic JSON archive remains usable. Existing applied-run
archives containing restore steps are supported; dry-run-only archives are
not eligible. Partial changes recorded by an interrupted or failed apply can
also be restored. REST operations are not atomic: a failed rollback may itself
leave partial changes. Its new archive retains the before state, proposed steps,
backout and available results. After resolving the failure, rerun the original
rollback input to finish restoration.

The input file is preserved. `--report` and `--report-dir` choose the new rollback
archive, and an output that would overwrite the input is rejected. Review the
preview before applying: restoration replaces the affected fields with archived
values, including when those fields have changed since the original run.
Unrelated settings are outside the restoration scope. No ConfigSync is issued.
WAF targets must still be confirmed user-created profiles with remote logging;
built-in profiles and targets no longer meeting that condition are refused.
