# nornir-netops

Push configuration to network devices listed in a CSV, one feature at a time,
from a per-platform Jinja template. **Dry run by default** -- without `--apply`
it connects read-only, works out the delta, and prints the exact commands it
would send.

```console
$ ./configure.py ntp --servers 10.50.0.10,10.50.0.11 --replace

DRY RUN -- no configuration will be changed  |  ntp, mode=replace, 2 device(s)
credentials: netauto via aws secret prod/network/netauto

atl-core-sw1 (10.1.10.11) [cisco_ios] would run (5 commands)
    ntp server 10.50.0.10
    ntp server 10.50.0.11
    no ntp server 10.10.10.1
    no ntp server 10.10.10.2 prefer
    write memory

atl-dc-leaf1 (10.1.20.21) [arista_eos] would run (5 commands)
    ntp server 10.50.0.10 iburst
    ntp server 10.50.0.11 iburst
    no ntp server 10.10.10.1 iburst
    no ntp server vrf MGMT 192.168.5.5 iburst
    write memory

summary: 2 device(s), 0 compliant, 2 with pending changes
re-run with --apply to push the commands above
```

| Subcommand | What it does | Platforms |
| --- | --- | --- |
| [`ntp`](#ntp) | Converge the NTP servers | `cisco_ios`, `arista_eos` |
| [`users`](#local-users) | Set local accounts and rotate their passwords | `cisco_ios`, `arista_eos` |
| [`snmp-packetsize`](#snmp-packet-size) | Set the SNMP maximum packet size | `cisco_ios` (EOS skipped -- no equivalent) |
| `selftest` | Render every template offline against sample output | -- |

Syslog and the rest slot in beside these without touching the engine -- see
[Adding a feature](#adding-a-feature).

### How this relates to the other tools here

[`scripts/ios/`](../ios/README.md) checks devices against the standards **held
in NetBox** and records a per-device verdict; [`scripts/f5/`](../f5/README.md)
maps `scripts/standards.yaml` onto BIG-IPs. This one is the fleet-push half and
deliberately has no NetBox dependency -- it takes a CSV of addresses and
converges one setting at a time. Reach for it when the question is "set this on
these devices", and for `scripts/ios/` when it is "which devices are out of
compliance".

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pip install boto3          # only if you keep credentials in AWS Secrets Manager
```

Python 3.9+. Nothing is written to the network until you pass `--apply`.

## The inventory CSV

One column with an address is all that is required. Copy
`inventory/hosts.csv.example` to `inventory/hosts.csv` and edit:

```csv
host,name,platform,port,username,password,site,role
10.1.10.11,atl-core-sw1,cisco_ios,,,,atl,core
10.1.20.21,atl-dc-leaf1,arista_eos,,,,atl,leaf
10.2.10.11,rdu-edge-rtr1,,,,,rdu,edge
```

| Column | Meaning |
| --- | --- |
| `host` | Management address. `hostname`, `ip`, `ip_address`, `address` and `mgmt_ip` also work. |
| `name` | Label used in output and `--limit`. Defaults to the address. |
| `platform` | `cisco_ios` or `arista_eos` (`ios`, `ios-xe`, `eos`, `arista` are accepted too). **Leave blank to autodetect over SSH.** |
| `port` | SSH port, if not 22. |
| `username`, `password`, `secret` | Per-device overrides for the boxes that are not on your central login yet. |
| anything else | Becomes host data, so `--filter site=atl` works with no code change. |

Duplicate names, rows with no address, and a missing address column are all
hard errors -- a typo should stop the run, not silently skip a device.

### How the platform is decided

The `platform` value is both the netmiko `device_type` used to connect **and**
the `templates/<platform>/` directory used to render, which is why aliases are
normalized to netmiko's spelling. Blank means autodetect: before anything is
pushed, netmiko's `SSHDetect` logs in, fingerprints the device, and writes the
answer onto the host. That costs an extra login per device, so fill the column
in for large runs. A device that detects as something with no template fails
with `platform 'cisco_nxos' has no 'ntp' support` rather than being sent IOS
syntax.

## Credentials

Resolved per field, first match wins:

1. `--username` / `--password` / `--secret` on the command line
2. **AWS Secrets Manager**, when `--aws-secret` is set
3. **Environment variables**, which include anything from your `.env`
4. A prompt, if you are on a terminal and only the password is missing

A `username`/`password` column in the CSV still overrides all of the above for
that one device.

### .env

`./.env` is picked up automatically (then `<project>/.env`). Copy
`.env.example`:

```bash
NET_USER=netauto
NET_PASS=...
NET_ENABLE=...            # only if login does not land in privileged mode
NETOPS_CSV=inventory/hosts.csv
```

Real environment variables win over the file, so a one-off
`NET_PASS=... ./configure.py ...` still works. `--no-env-file` ignores it
entirely.

### AWS Secrets Manager

Point at a JSON secret and name the fields inside it. Auth uses the default
boto3 chain, so an EC2 instance profile or ECS task role needs no keys on disk:

```bash
./configure.py ntp --servers 10.50.0.10 \
  --aws-secret prod/network/netauto --aws-region us-east-1
```

For a secret shaped `{"username": "...", "password": "...", "enable_secret": "..."}`
the defaults are correct. Otherwise name the keys:

```bash
--aws-username-key user --aws-password-key pw --aws-enable-key enable
```

Every one of these has an environment equivalent (`NET_AWS_SECRET`,
`NET_AWS_REGION`, `NET_AWS_USERNAME_KEY`, ...), so a cron job can carry the
whole configuration in its `.env` and run bare:

```bash
NET_AWS_SECRET=prod/network/netauto ./configure.py ntp -s 10.50.0.10 --fail-on-diff
```

The password is never printed. The banner names the identity and where it came
from, nothing more.

### SSH keys

`--key-file ~/.ssh/id_ed25519` (or `$NET_KEY_FILE`) authenticates with a key
instead of a password.

## Running it

```bash
# dry run: read-only, prints the commands it would send  (the default)
./configure.py ntp --servers 10.50.0.10,10.50.0.11

# add those servers, leave anything else configured in place
./configure.py ntp --servers 10.50.0.10,10.50.0.11 --add --apply

# make those the only NTP servers -- everything else is removed
./configure.py ntp --servers 10.50.0.10,10.50.0.11 --replace --apply

# one site, one device, more parallelism, a JSON record of what happened
./configure.py ntp -s 10.50.0.10 --filter site=atl --apply
./configure.py ntp -s 10.50.0.10 --limit atl-core-sw1 --apply
./configure.py ntp -s 10.50.0.10 --workers 25 --apply --report run.json
```

| Flag | Effect |
| --- | --- |
| *(none)* | **Dry run.** Connects read-only, prints the plan, changes nothing. |
| `--add` | Add the desired entries; leave every other one alone. The default mode. |
| `--replace` | Add the desired entries **and remove every other one**. |
| `--apply` | Actually push. Prompts once on a terminal; `-y` skips the prompt. |
| `--no-save` | Skip `write memory` after a successful change. |
| `--no-verify` | Skip the read-back that confirms the change landed. |
| `--fail-on-diff` | Exit 2 if anything is out of compliance. For a cron drift check. |
| `-v` | Also show current state and raw device output. |

Exit codes: `0` all good, `1` a device failed or could not be verified, `2`
drift found with `--fail-on-diff`, `3` a usage or credential problem.

---

## ntp

```bash
./configure.py ntp --servers 10.50.0.10,10.50.0.11 [--replace] [--apply]
```

`--vrf MGMT`, `--prefer 10.50.0.10`, `--source Loopback0`, `--no-iburst` (Arista).

Reads `show running-config | include ^ntp server`, which deliberately cannot
see `ntp source`, `ntp authenticate` or `ntp access-group`, so `--replace` can
never remove those. Removal negates the device's own line, so a server
configured with options this tool does not model still goes away cleanly.

## Local users

```bash
# rotate the password on one account, everywhere
./configure.py users --user admin --privilege 15 --apply

# onboard an account without touching the ones that already exist
./configure.py users --user netauto --only-missing --apply

# make these the only local accounts on the box
./configure.py users --user admin,netauto --replace --apply
```

```console
atl-core-sw1 (10.1.10.11) [cisco_ios] would run (3 commands)
    current:
      username admin privilege 15 password 7 (weak)
      username netauto privilege 15 secret 9
    no username admin
    username admin privilege 15 secret <redacted>
    write memory
```

**Every managed account is negated and rewritten in the same config push.**
Neither IOS nor EOS reliably accepts a new hash type on top of an old one -- a
legacy `username x password 7 ...` will not simply become `secret 9 ...` -- so
`no username x` goes out immediately before the replacement. That is also what
a rotation is, which is why this feature never reports "already compliant" for
an account it manages: the hash is salted, so there is nothing to compare
against. Use `--only-missing` when you want existing accounts left alone.

### Where the password comes from

Never from the command line. Per account, first match wins:

1. `--password-secret NAME` -- an AWS secret holding a JSON object of
   `{"admin": "...", "netauto": "..."}` (`$NETOPS_PW_SECRET`), read with the
   same ambient IAM identity as the device login
2. `$NETOPS_PW_<ACCOUNT>` -- from the environment or the `.env`, e.g.
   `NETOPS_PW_ADMIN`, `NETOPS_PW_NET_AUTO_SVC`
3. A prompt, asked twice, if you are on a terminal

```bash
# unattended, from Secrets Manager
./configure.py users -U admin -U netauto \
  --password-secret prod/network/local-users --apply -y

# or from the .env
NETOPS_PW_ADMIN=... ./configure.py users -U admin --apply
```

The value reaches the device and nowhere else: rendered commands, the JSON
report, and the device's own echo are all scrubbed to `<redacted>` before
anything is printed or written.

| Flag | Effect |
| --- | --- |
| `-U, --user NAME[,NAME...]` | Accounts to manage. Repeatable. Case sensitive. |
| `--privilege` | Privilege level, default `15`. |
| `--role` | EOS role, e.g. `network-admin`. |
| `--algorithm md5\|sha256\|scrypt` | IOS `algorithm-type` for a plaintext password (`scrypt` is type 9). |
| `--hash-type TYPE` | The supplied value is already a hash: `5`, `8`, `9` on IOS, `5` or `sha512` on EOS. |
| `--only-missing` | Create absent accounts only; never rotate an existing one. An ssh-key on a managed account is still removed. |
| `--allow-remove-self` | With `--replace`, also purge the account this run is logged in as. |

Safety rails, all covered by tests:

- **`--replace` never purges the account you are logged in as** unless you pass
  `--allow-remove-self`. That is the one mistake with no remote recovery.
- Plaintext passwords must be at least 8 characters. A value that looks like a
  hash is rejected unless `--hash-type` says so, because the platforms spell
  that keyword differently and sending it as plaintext would set the password
  to the literal hash string.
- The read-back after `--apply` is what catches a `no username x` whose
  replacement did not land. If an account is missing afterwards the device is
  reported `APPLIED BUT NOT VERIFIED`, **startup-config is not saved**, and the
  run exits 1 -- so the account survives a reload while you fix it.
- An EOS account's `ssh-key` lives on its own `username x ssh-key ...` line and
  is an alternative credential that bypasses the password being managed here,
  so it is **negated explicitly** -- `no username x ssh-key` first, while the
  account still exists, then `no username x`. That holds whether or not EOS
  cascades the account removal to the key line, and it applies even under
  `--only-missing`, where the account's password is otherwise left alone.

## SNMP packet size

```bash
./configure.py snmp-packetsize --apply           # 1300 by default
./configure.py snmp-packetsize --size 1400 --apply
```

Caps the largest SNMP payload IOS will emit, keeping big GETBULK replies inside
the path MTU instead of relying on fragmentation that firewalls and tunnels
tend to drop. Valid range 484-17940; the platform default is 1500 and is not
written to the running config, so a default device reads as having nothing set.
Idempotent -- once the value matches, the device reports compliant.

**Arista is reported as skipped, not failed.** EOS has no `snmp-server
packetsize` equivalent, so it is declared not-applicable with that reason
rather than being sent IOS syntax:

```console
atl-dc-leaf1 (10.1.20.21) [arista_eos] skipped -- EOS has no `snmp-server packetsize` equivalent
summary: 2 device(s), 1 compliant, 1 with pending changes, 1 not applicable
```

That claim is from documentation, not from a device on your network -- confirm
it against your EOS release before treating a skipped switch as compliant. If
your release does have an equivalent, add a template and a parser and drop the
`not_applicable` entry.

---

## How it decides

1. Read the current state with a narrow `show ... | include` so the feature can
   only ever see -- and therefore only ever remove -- its own configuration.
2. Compare. Most features compare on the **normalized** value, so `010.1.1.1`,
   `10.1.1.1` and `TIME.example.net` do not push a duplicate every run. A
   feature can supply its own planner instead: `users` rewrites unconditionally,
   `snmp-packetsize` compares a scalar.
3. Render the platform's template for what is missing, and negate what is left
   over (`--replace`).
4. Push with netmiko.
5. **Read the config back** and confirm every desired entry is now present. If
   not, report it loudly and do **not** save. `--no-verify` skips this.
6. `write memory`, unless `--no-save`.

## Templates

One template per platform per feature, in `templates/<platform>/<feature>.j2`:

```jinja
{% for server in add %}
ntp server {% if vrf %}vrf {{ vrf }} {% endif %}{{ server }}{% if prefer and server == prefer %} prefer{% endif %}
{% endfor %}
{% for entry in remove %}
no {{ entry.line }}
{% endfor %}
```

`add` is the list of values to configure, `remove` is a list of entries whose
`.line` is what gets negated, and the rest of the variables come from the
feature's CLI flags. Order matters and is the template's to decide -- the
`users` templates put the negations first. Blank lines are dropped, so lay them
out readably. Undefined variables are an error rather than a silently missing
keyword.

After editing one, render it offline against sample device output -- no
credentials, no devices:

```bash
./configure.py selftest
```

Templates are read from `./templates` in the checkout. `$NETOPS_TEMPLATES`
points elsewhere, which is how a non-editable `pip install` finds them.

## Adding a platform

1. `templates/<platform>/<feature>.j2` -- the syntax for that OS.
2. Add it to `FEATURE.platforms` in the feature module with its show command, a
   parser, and a sample of real output.
3. Add its save command to `SAVE_COMMANDS` in [`netops/core.py`](netops/core.py).
4. `./configure.py selftest` to see it render, then `pytest`.

Use the netmiko platform name (`cisco_nxos`, `juniper_junos`, ...) so the
connection and the template directory agree. If the platform genuinely has no
equivalent setting, list it in `not_applicable` with the reason instead -- those
devices are skipped and reported, not failed.

## Adding a feature

Syslog, for example:

1. `netops/features/syslog.py` -- a `parse` for `show run | include ^logging host`,
   the CLI flags, a `build_desired`, and a `FEATURE = Feature(...)`. Add a
   `plan=` only if the change is not a set difference.
2. `templates/cisco_ios/syslog.j2` and `templates/arista_eos/syslog.j2`.
3. Add it to `FEATURES` in [`netops/features/__init__.py`](netops/features/__init__.py).

It gets `configure.py syslog` with the same dry run, `--add`/`--replace`,
credential handling, filtering, verification, secret scrubbing and reporting --
none of which lives in the feature.

## Tests

```bash
pip install pytest
pytest
```

192 tests, no network. `tests/test_run.py` drives the real CLI, inventory,
runner and templates end to end against a stateful fake device, so an apply is
followed by a genuine read-back -- including the checks that a password reaches
the device and never the terminal, the report, or the logs.

## Safety notes

- Dry run is the default and is read-only; `--apply` prompts before the first
  change unless `-y` or a non-interactive shell.
- `--replace` removes configuration. Read the dry run first.
- Addresses, usernames, interface names and passwords are validated before they
  reach a template, so nothing can smuggle a second command onto a line.
- One device failing does not stop the others; it is reported and the run exits 1.
- `.env` and `inventory/hosts.csv` are gitignored. Keep real credentials in AWS
  Secrets Manager where you can.
