# Cisco IOS configuration standards

Checks Cisco IOS and IOS-XE devices against the configuration standards held in
NetBox, records a per-device verdict, and — only when told to — fixes what it
found.

The standards are **not** in a file here. They live in NetBox, in the Compliance
plugin ([`plugins/netbox-compliance`](../../plugins/netbox-compliance)), because
the point of the exercise is that "how many devices still have `ip http server`
on?" should be a report rather than a script somebody runs against the whole
fleet while reading terminal output. This tool is the half that can reach a
switch; NetBox is the half that remembers.

(`scripts/f5/` does the same job for BIG-IP from `scripts/standards.yaml`. That
file is platform-neutral values — SNMP pollers, syslog collectors — applied to
whatever config a platform expresses them in. These standards are different in
kind: they are assertions about IOS configuration lines, they are per-device
rather than fleet-wide, and each one carries a verdict per device. That is why
they are records in NetBox rather than more YAML.)

## Setup

```bash
cd scripts/ios
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env     # NetBox URL/token, IOS username/password
```

`.env` is gitignored. Environment variables already exported in the shell beat
it, so a CI job or a vault wrapper can supply `IOS_PASSWORD` without writing it
to disk. Point at a different file with `--env-file`.

The NetBox token needs **view** on config standards and **add + change** on
config compliance. The IOS account needs privilege 15: reading
`show running-config` does on most estates, and `--update`/`--enforce`
certainly do.

### Why netmiko

This repo's tools otherwise get by on `requests`. Classic IOS has no REST API,
so this one is SSH, and SSH to a network device is not a socket and a string:
it is prompt detection, `terminal length 0` to stop paging, enable mode when the
account does not land in privileged exec, config mode, and reading back the
result of a command that may or may not have taken. netmiko handles all of it
and is the standard answer in this space. Hand-rolling that against a device
that will happily half-apply a change is not a saving.

It is pinned in `requirements.txt` beside the script, the way `scripts/f5/` pins
`requests` and `PyYAML`. Nothing else here needs it: the NetBox client is
`urllib`, and everything that makes a decision is pure Python in `iosconfig.py`.

## The three modes

**A plain run changes nothing on a device.**

| | reads the device | writes to the device | writes results to NetBox |
|---|---|---|---|
| audit (default) | yes | no | yes |
| `--update` | yes | only with `--commit`, additions only | yes |
| `--enforce` | yes | only with `--commit`, additions and removals | yes |

```bash
./ios_standards.py --poller boston                     # audit the poller's devices
./ios_standards.py --device lab-sw-01                  # one device
./ios_standards.py --site boston --update              # show what it would add
./ios_standards.py --site boston --update --commit     # add it
./ios_standards.py --device lab-sw-01 --enforce --commit
./ios_standards.py --poller boston --only "No HTTP server"
./ios_standards.py --poller boston --no-report         # do not write to NetBox
```

`--commit` gates writes to **devices**. Results go to NetBox on every run unless
`--no-report`, because the compliance record is the deliverable rather than a
side effect — a run that found drift and told nobody has not done the job.

**`--update` will not turn off the HTTP server.** That is the definition
working as intended, and it surprises people, so: update *adds what the standard
says should be there and never removes anything*. An "absent" standard's correct
state is expressed as a removal, so update reports it and leaves it alone.
`--enforce` is what removes configuration.

**Enforce is opt-in per standard.** Even `--enforce --commit` does nothing for a
standard whose "Allow enforce" box is unticked in NetBox, and everything the
seeder creates starts unticked. A global switch that suddenly begins deleting
local accounts across a fleet is exactly the accident worth engineering against.

Exit codes, matching `scripts/f5/f5_standards.py`: **0** compliant or committed,
**1** a device failed, **2** drift found but not committed. A cron audit can
tell "all good" from "needs work" without parsing output.

## Safety

Enforce removing local accounts can lock somebody out of a production switch.
These are not options:

- **The account this session authenticated as is never removed.** Whatever the
  standard says.
- **The last privilege-15 local account is never removed.** Evaluated against
  the state *after* this run's additions, so replacing an old admin with a new
  one in a single run is allowed — and refused if the new one could not be
  built.
- **A failed addition cancels the removals.** If a secret was not supplied and
  an account the standard wants could not be created, nothing is removed. This
  is the case that turns a tidy-up into a lockout.
- **Additions are sent before removals, and the device is re-read in between.**
  A removal only proceeds once the replacement is confirmed present on the
  device — not once the add command was sent without an error.
- **The governed configuration is captured before anything is written**,
  redacted, and stored on the compliance record as a rollback reference. Only
  the sections the standard governs: a full running-config in NetBox would be a
  copy of every secret on the box.
- **Every command is shown before it is sent**, redacted, and nothing is sent
  without `--commit`.
- **Anything refused is reported with a reason.** A plan that silently drops a
  remediation looks exactly like a plan with nothing to do.

## Redaction

Several of these standards match lines whose entire content is a credential.
Nothing leaves this process unredacted — not the terminal output, not the
`observed` text stored in NetBox, not the command log, not an exception message.
Each command carries two forms: the text that is sent and a redacted `display`,
and only the SSH write path touches the former.

The redactor is deliberately blunt. It replaces anything credential-shaped
whether or not it understands the command it appeared in, because a redactor
that only handles the syntax it was taught fails silently on the line nobody
anticipated, and that failure ends with a password hash in a database.
`tests/test_parsing.py` asserts that no hash from the fixture config survives.

## The five standards

Seeded into NetBox with `manage.py create_config_standards` (run it in the
NetBox container; `--platform ios-xe` scopes them, `--dry-run` shows what it
would create). Each is editable afterwards, and the seeder never overwrites one
that already exists — a standard is an operational document, and a command that
silently rewrites one somebody had adjusted is its own kind of outage.

| Standard | Type | Remediation |
|---|---|---|
| No HTTP server | absent | enforce only (`no ip http server`) |
| No HTTPS server | absent | enforce only (`no ip http secure-server`) |
| Password encryption service | present | update adds `service password-encryption` |
| No type-7 passwords | absent | **none — audit only** |
| Local users | exact set | update adds; enforce also removes |

**Why "No type-7 passwords" is separate from "Password encryption service".**
`service password-encryption` only produces type 7, which is a Vigenère cipher
with a published key — encoding, not encryption. Its presence is a checkbox an
auditor asks for and a defence against shoulder-surfing; it is not protection.
Collapsing the two would mean passing the easy half reads as passing both.

**Why "No type-7 passwords" is never remediated automatically.** Converting
`password 7 <hash>` to a `secret` needs the plaintext. Type 7 is reversible, so
a tool *could* decrypt each one and re-set it. It will not: silently
round-tripping production credentials through a script — into memory, and
possibly into a log on the way — is not something to do because it happens to be
technically possible. The standard carries `auto_remediable = false`, the plan
refuses in every mode, and the report flags the device as needing a person.
Fix each by hand with the plaintext you already hold:

```
enable secret <plaintext>                  ! replaces enable password
username <name> secret <plaintext>         ! replaces username ... password
line vty 0 15
 no password
 login local
```

Type 8 or 9 is the target; type 5 is the floor.

**The local-users standard is seeded with one example account** (`netops`,
privilege 15). It is almost certainly not your account list — edit it before
checking anything, or every device reports the wrong drift. Seed real ones with
`--local-user netops:15 --local-user backupadm:15`.

## Remediation secrets

A standard that says "these local accounts should exist" cannot hold their
passwords in NetBox. So the remediation is a template —
`username {key} privilege {privilege} secret {secret}` — where `{key}` and
`{privilege}` come from the standard and `{secret}` comes from this tool's
environment at the moment of the write:

```bash
IOS_ACCOUNT_SECRET=...        # used for every account a standard adds
IOS_SECRET_NETOPS=...         # used for `netops` specifically, beating the above
```

The standard's page in NetBox lists which variables it will ask for. Without
one, the addition is **refused and reported** — never rendered half-way and
sent — and, because a missing account is the lockout case, the removals for that
standard are cancelled too.

## Which devices

`--device`, `--site`, `--platform` and `--role` filter NetBox directly.
`--poller <name>` uses the ownership chain the discovery work already
established:

```
device `poller-<name>` tag  >  site's tag  >  nearest tagged ancestor region
```

That precedence is shared with `scripts/snmp-inventory/snmpinv/selection.py`
(which addresses are mine?) and
`plugins/netbox-discovery/netbox_discovery/resolution.py` (whose job is this
address?). All three must agree, so this one walks regions upwards the same way
and treats any `poller-` tag that is not ours as somebody else's claim —
structurally, so standing up a new poller never means editing an existing
poller's configuration.

There is deliberately **no "everything" default**: a selector is required.

*Which standards* apply to a device is not decided here at all — it is asked of
NetBox (`?device_id=`). "An empty scope dimension means no restriction" is a
rule with an edge to get wrong, and the fleet report applies it too; one
implementation means the report and the checker cannot disagree about what a
device is being measured against.

`--host` connects to an address directly — for a box reachable at an address
NetBox does not have, or one being checked before it is onboarded. Add
`--device-name <name>` and it is scoped and recorded exactly like any other
device; without it there is no record to scope against, so every active standard
is checked and the result is not written to NetBox (there would be nothing to
attach it to). The run says which of the two happened.

## Testing

There is no real switch in the lab, so there is an emulated one: a paramiko SSH
server replaying a recorded `show running-config`, which netmiko connects to
over a real SSH session on loopback. Config commands are applied to the
in-memory configuration, so remediation is observable — send `no ip http server`
and the next `show running-config` shows it gone — and every command received is
recorded, which is what lets a test assert that a guard *prevented* a command
rather than merely that a plan said it would.

```bash
pip install pytest
python -m pytest scripts/ios/tests -q
```

What that proves: the SSH path end to end (prompt detection, paging, config
mode, `write mem`), parsing, redaction, evaluation of all five standards,
remediation planning, every lockout guard, and the exact payload posted to
NetBox. What it does not prove: that a particular IOS release accepts a
particular command. Only a real switch can tell you that; the fixtures encode
what a C9300 running 17.9 is documented to produce. When a real device is
available, capture its config and drop it in over `tests/fixtures/*.cfg` — the
format is literally `show running-config` output.

`tests/test_shared_contract.py` pins the two places this design is necessarily
duplicated — the template substituter and the five shipped standards — against
the plugin's own source, so editing one and not the other fails a test rather
than producing a standard that validates in the UI and renders differently on a
switch.

## What this does not do

- **Attribute drift on a governed set is not compared.** The local-users
  standard decides membership by account name: an account that exists at the
  wrong privilege level is compliant. Fixing that means re-issuing the account,
  which needs the secret, so it is a deliberate boundary rather than an
  oversight.
- **Banner bodies are not parsed specially.** A banner containing text that
  looks like configuration can produce a false match. No standard here is
  affected; it is written down so the next one accounts for it.
- **Nothing is scheduled.** Run it from cron or a runner; exit codes are
  designed for that.
- **It does not read startup-config.** Everything is measured against the
  running configuration, and `--commit` saves after a successful change.
