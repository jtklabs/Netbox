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
| [`syslog`](#syslog) | Collectors, trap severity, source interface | `cisco_ios`, `arista_eos` |
| [`banner`](#banner) | Login and MOTD banners | `cisco_ios`, `arista_eos` |
| [`acl`](#acls) | Access lists, **order enforced** | `cisco_ios`, `arista_eos` |
| [`users`](#local-users) | Local accounts and password rotation | `cisco_ios`, `arista_eos` |
| [`snmp`](#snmp) | v3 users, groups, views, hosts; removes v2c | `cisco_ios`, `arista_eos` |
| [`snmp-packetsize`](#snmp-packet-size) | SNMP maximum packet size | `cisco_ios` (EOS skipped -- no equivalent) |
| [`check-ntp`](#is-it-actually-working) | Are the NTP servers associated, reachable and selected? Read-only | `cisco_ios`, `arista_eos` |
| `discover` | Detect each device's platform and remember it; changes nothing | -- |
| `selftest` | Render every template offline, and check the standards file | -- |

What each of them should converge on comes from
[the standards file](#the-standards-file); flags override it. A new domain is a
feature module plus two templates -- see [Adding a feature](#adding-a-feature).

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
answer onto the host. A device that detects as something with no template fails
with `platform 'cisco_nxos' has no 'ntp' support` rather than being sent IOS
syntax.

Autodetection is the slow part of a run -- `SSHDetect` opens a whole extra SSH
session per device, before the real task connects again -- so the answer is
remembered for 24 hours:

```console
$ ./configure.py discover

detecting platform on 3 device(s), 3 at a time...

  atl-core-sw1   cisco_ios      from the CSV
  atl-dc-leaf1   arista_eos     detected
  rdu-edge-rtr1  cisco_ios      detected
  rdu-old-box    unknown        could not autodetect the platform; set the platform column in the CSV

summary: 4 device(s), 1 from the CSV, 2 detected, 1 unidentified
remembered in .platform-cache.json for 24h
```

`discover` connects, works out what each device is, writes the cache and
changes nothing -- it does not even read a device's configuration. Run it once
after adding devices to the CSV and every feature run afterwards starts warm.
Ordinary runs populate the cache too; `discover` just does it deliberately.

| Flag | Effect |
| --- | --- |
| `--platform-cache-ttl HOURS` | How long an answer stays good. Default 24. |
| `--no-platform-cache` | Ignore what was remembered and detect again. |
| `--platform-cache FILE` | Where to keep it [`$NETOPS_PLATFORM_CACHE`], default `.platform-cache.json` (gitignored). |
| `discover --refresh` | Detect again even for devices already remembered. |

Two things it deliberately does not do. **A `platform` column always wins** --
an explicit statement is not something to second-guess, and those devices are
never detected or cached at all. And **the cache only ever fills a blank**, so
it can never override the CSV.

The staleness window is the trade: a device whose platform genuinely changes --
re-purposed hardware, an IOS box swapped for EOS -- is wrong until the entry
expires. That fails loudly rather than quietly, because the device rejects the
show command and the run stops there; `--no-platform-cache` or
`discover --refresh` fixes it immediately.

## NetBox as the inventory

The CSV stays the default; `--netbox` reads devices from NetBox instead:

```bash
./configure.py ntp --netbox --netbox-filter site=atl --netbox-filter role=core
```

Active devices with a primary IP become hosts. Site, role, tags and device
custom fields land in host data, so `--filter site=atl` works exactly as it
does with a CSV. A device with no platform is autodetected as usual; one with
no primary IP is skipped, because there is nothing to connect to.

### Platforms

`scripts/snmp-inventory` sets a NetBox Platform per OS family -- "Cisco IOS",
"Cisco IOS-XE", "Cisco NX-OS", "Arista EOS", "Junos" -- and NetBox slugifies
the name. Those slugs rarely resemble the netmiko driver, so the mapping is
explicit rather than a hyphen-to-underscore guess:

| NetBox platform | slug | connects as |
| --- | --- | --- |
| Cisco IOS | `cisco-ios` | `cisco_ios` |
| Cisco IOS-XE | `cisco-ios-xe` | `cisco_ios` -- one driver and one template cover both |
| Cisco NX-OS | `cisco-nx-os` | `cisco_nxos` (**not** `cisco_nx_os`) |
| Cisco ASA | `cisco-asa` | `cisco_asa` |
| Arista EOS | `arista-eos` | `arista_eos` |
| Junos | `junos` | `juniper_junos` |
| PAN-OS | `pan-os` | `paloalto_panos` |
| FortiOS | `fortios` | `fortinet` |
| F5 TMOS | `f5-tmos` | `f5_tmsh` |
| Check Point Gaia | `check-point-gaia` | `checkpoint_gaia` |
| ArubaOS / ArubaOS-CX | `arubaos`, `arubaos-cx` | `aruba_os`, `aruba_aoscx` |
| Opengear | `opengear` | `opengear_linux` |

Anything not in the table falls back to the generic rule. A test checks every
mapped driver against netmiko's own dispatcher, so a typo -- or netmiko
renaming one -- fails here rather than on a device.

**Only `cisco_ios` and `arista_eos` have templates today.** The rest are mapped
anyway, deliberately: a NX-OS device then stops with `platform 'cisco_nxos' has
no 'ntp' support` without ever being dialled, which is accurate and free.
Leaving them blank would instead spend an autodetect login finding out what
NetBox already said.

So on a mixed fleet, filter to what this tool can drive:

```bash
./configure.py ntp --netbox \
  --netbox-filter platform=cisco-ios \
  --netbox-filter platform=cisco-ios-xe \
  --netbox-filter platform=arista-eos
```

A repeated key means "any of these" -- that is how NetBox reads repeated query
parameters.

`$NETBOX_URL` and `$NETBOX_TOKEN`, or `--netbox-secret` for an AWS secret
holding `{"token": "...", "url": "..."}`. The URL may also sit in the standards
file under `netbox.url`; the **token never does**, because that file is meant
to be committed.

### A source interface is a property of the device

One switch sources syslog from Loopback0, another from Vlan10, a third from
nothing at all. That is not a fleet-wide standard, so it is not in the
standards file -- it is a boolean custom field on the *interface* in NetBox:

```
ntp_source_interface      true   on Loopback0
syslog_source_interface   true   on Vlan10
```

The rule that follows from a boolean:

| Interfaces marked | Result |
| --- | --- |
| none | **The device uses no source interface** -- and this overrides the fleet-wide `ntp.source` in the standards file. "Not set" is an answer, not a gap to fill. |
| exactly one | That interface is the source. |
| two or more | **That device fails**, naming the interfaces. |

```console
sw1 (10.1.1.1) [cisco_ios] FAILED -- 2 interfaces are marked ntp_source_interface
    in NetBox (Loopback0, Vlan10); exactly one may be
```

The failure is per device and per standard: the rest of the fleet is planned
normally, and a device whose `ntp_source_interface` is ambiguous can still have
its syslog source applied. It is caught **before the device is read or
written**, and picking one would be guessing -- the wrong source interface is
the kind of thing that quietly breaks return traffic rather than failing
visibly.

The custom fields consulted default to `ntp_source_interface` and
`syslog_source_interface`; `--netbox-source-field` or `netbox.source_fields` in
the standards file changes that. The part before `_source_interface` names the
feature the answer belongs to, so `snmp_source_interface` would wire itself to
`snmp` once that feature learns to use one.

NetBox is asked **once per custom field for the whole fleet**, not once per
device -- a single query the server is built to answer, rather than a round
trip per device per standard.

### What this means for the templates

A source interface is now part of what makes a line correct, so it is part of
the comparison: `ntp server 10.50.0.10 source Loopback0` and
`ntp server 10.50.0.10` are different states. Without that, a stale
`source Loopback0` on a device that should no longer have one would read as
compliant forever. Re-issuing the line replaces it; it is never negated first,
for the same reason as the auth key.

## The standards file

The CSV says *which* devices. `standards.yaml` beside this tool says *what*:

```yaml
ntp:
  servers: [10.50.0.10, 10.50.0.11]

syslog:
  destinations:
    - 10.1.1.50          # port 514
    - host: 10.1.1.51    # or a non-default port
      port: 1514
  severity: informational

snmp:
  allow: [10.1.1.0/24, 10.2.0.0/16]   # networks allowed to poll
  acl: SNMP-POLLERS                   # the ACL that enforces `allow`
  communities: []                     # v2c: none permitted, remove any found
  location: "ATL DC1 - row 4"
  users:
    - name: nmsuser
      group: NMS-RO
      auth: sha
      priv: aes 128

acls:
  - name: SNMP-POLLERS
    permit: snmp.allow    # points at the networks above, not a second copy
    deny_log: true

local_accounts:
  names: [admin, netauto]
```

**`standards.yaml.example` is what ships; the real `standards.yaml` is
gitignored**, so pulling this tool never overwrites yours:

```bash
cp standards.yaml.example standards.yaml
```

Keep your real one under version control somewhere -- the standard belongs
under change control -- but not here. There are no credentials in it either
way. YAML or JSON; `--standards FILE` or `$NETOPS_STANDARDS` names a different
one, `--no-standards` ignores it.

A real run **never** falls back to the example: its addresses are placeholders,
and converging a fleet onto them would be far worse than stopping with a
message. `configure.py selftest` is the one exception, because it renders
templates offline and touches nothing -- so a fresh clone can still check
itself.

Three things worth knowing:

- **A flag always wins.** `./configure.py ntp --servers 10.9.9.9` ignores
  `ntp.servers`, so a one-off run never needs the file edited.
- **An empty list is a statement.** `communities: []` means *there must be no
  v2c community*, and any found on a device is removed. Saying nothing about
  communities leaves them alone. The two are different, and the file can say
  either.
- **A dotted path is a reference.** `permit: snmp.allow` resolves to the
  networks under `snmp.allow`, so adding a poller subnet is one edit in one
  place rather than two that can drift apart. A value that merely looks like a
  path but does not resolve -- `time.example.net` -- stays a string.

An unknown section warns rather than fails, so the file can carry a section
this tool does not own yet. `./configure.py selftest` renders every template
against the real file, which makes it a check of the file itself.

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
| `--standards FILE` | The desired state file. `--no-standards` ignores it. |
| `--open-change` / `--change` | [ServiceNow change records](#servicenow-change-records). |
| `--fail-on-diff` | Exit 2 if anything is out of compliance. For a cron drift check. |
| `-v` | Also show current state and raw device output. |
| `-w, --workers` | How many devices to work on at once. Default 10. |
| `--conn-timeout` | Seconds to wait for the TCP connection before giving up on a device. Default 10. |
| `--log-file`, `--no-log-file`, `--debug` | See [When a device fails](#when-a-device-fails). |

Exit codes: `0` all good, `1` a device failed or could not be verified, `2`
drift -- either found with `--fail-on-diff`, or drift the tool declined to fix
(see [ACLs](#acls)), `3` a usage or credential problem, `130` interrupted with
Ctrl-C.

### Concurrency

Devices are worked in parallel by nornir's threaded runner -- `--workers`,
default 10. Sessions are almost entirely waiting on the network, so raising it
for a big CSV is cheap:

```bash
./configure.py ntp -s 10.50.0.10 --workers 50
```

The platform autodetect pass is threaded the same way. The banner says what is
actually happening, capped at the number of devices in the run:

```
DRY RUN -- no configuration will be changed  |  ntp, mode=add, 240 device(s), 50 at a time
```

An unreachable device holds its worker for `--conn-timeout` seconds and then
frees it, so a handful of dead addresses slows a run by seconds, not minutes.

### When a device fails

One line per device, and the run continues:

```console
atl-dc-leaf1 (10.1.20.21) [arista_eos] already compliant

atl-core-sw1 (10.1.10.11) [cisco_ios] would run (2 commands)
    ntp server 10.50.0.10
    write memory

atl-core-sw2 (10.1.10.12) [cisco_ios] FAILED -- timed out connecting -- unreachable, filtered, or wrong port

rdu-edge-rtr1 (10.2.10.11) [cisco_ios] FAILED -- authentication failed -- check username, password or enable secret

summary: 4 device(s), 1 compliant, 1 with pending changes, 2 failed
full detail in netops-debug.log
```

Devices are ordered quiet-first, so the ones needing attention sit next to the
summary rather than scrolled off the top.

The full exception and traceback -- netmiko's nine-line "common causes"
explanation, nornir's per-task traceback, all of it -- goes to
`netops-debug.log` instead of the terminal. The file is only created when there
is something to record, so a clean run leaves nothing behind.

| Flag | Effect |
| --- | --- |
| `--log-file FILE` | Where to write it. Default `netops-debug.log` [`$NETOPS_LOG_FILE`]. |
| `--no-log-file` | Do not record it. The terminal stays one-line-per-device either way. |
| `--debug` | Print the tracebacks as well, and log the full SSH transcript (netmiko/paramiko at DEBUG) -- the thing to reach for when "timed out" is not enough. |

Ctrl-C during a run stops without a traceback and exits 130.

---

## ntp

```bash
./configure.py ntp --servers 10.50.0.10,10.50.0.11 [--replace] [--apply]
```

`--vrf MGMT`, `--prefer 10.50.0.10`, `--source Loopback0`, `--no-iburst` (Arista).

Removal negates the device's own line, so a server configured with options this
tool does not model still goes away cleanly. `ntp source`, `ntp master` and
`ntp access-group` are never parsed, so `--replace` can never remove them.

### Authentication

```yaml
ntp:
  servers: [10.50.0.10, 10.50.0.11]
  authentication:
    key_id: 1
    type: md5
    trusted: true      # ntp trusted-key <id>
    enable: true       # ntp authenticate
```

```
ntp authentication-key 1 md5 <redacted>
ntp trusted-key 1
ntp server 10.50.0.10 key 1
ntp server 10.50.0.11 key 1
ntp authenticate
```

**The order is the point.** The key and its trusted-key entry go first, then
the servers that reference it, and `ntp authenticate` last -- so a device is
never told to demand authentication it cannot yet satisfy.

The key binding is part of a server's identity: a server configured without its
key is not the same as one with it, so the line is re-issued. It is *not*
negated first -- re-issuing replaces it, and negating afterwards would delete
the server that had just been corrected.

The key material never goes in the standards file. It comes from
`$NETOPS_NTP_KEY_<id>` or a `--key-secret` AWS secret shaped `{"1": "..."}`,
and is scrubbed from every command list, report and device echo. **It cannot be
read back** -- IOS stores it type-7 encrypted -- so only the key id and
algorithm are compared, and a changed key is invisible. Push one with:

```bash
./configure.py ntp --apply --rewrite-keys
```

**Silence is not a statement.** A standards file that says nothing about
`authentication:` will not have `--replace` tear authentication off devices
that have it; only a file that declares the section manages those lines.

## Syslog

```bash
./configure.py syslog --apply                       # from the standards file
./configure.py syslog -d 10.9.9.9:1514 --apply      # one-off collector
```

Manages four kinds of line -- `logging host`, `logging trap`,
`logging source-interface` and `logging origin-id` -- and deliberately parses
nothing else, so the many other lines `show running-config all` returns
(`logging buffered`, `logging console`, `logging facility`) can never be
removed by `--replace`.

Reads `show running-config all`, because a severity at the platform default is
otherwise invisible -- see
[Settings that sit at their default](#settings-that-sit-at-their-default).

```yaml
syslog:
  destinations: [10.1.1.50]
  severity: informational
  origin_id: hostname     # or ip, ipv6, or any other text -> `string <text>`
```

`origin_id` prepends an identifier to every message sent to a collector, so the
source is obvious in the aggregator. **Cisco only** -- EOS has no
`logging origin-id`, and its nearest relative (`logging format hostname ...`)
is a different setting rather than a spelling of this one. EOS devices simply
do not get the line, rather than being reported out of compliance forever over
something they cannot have.

The severity is compared *by value*: a device on `notifications` and a standard
of `informational` are different, which is what makes the change appear. But
the severity and source interface are never negated. `no logging trap
notifications` clears the setting whatever argument it is given, so negating a
stale one after setting the new value would undo the change -- setting it
replaces the old value by itself. Only collectors are removed by `--replace`.

## Banner

```bash
./configure.py banner --apply
```

**The text lives in `templates/<platform>/banner.j2`** -- edit it there, where
it is reviewed in the same diff as everything else. The standards file only
says which banners are managed (`banner.motd: true`).

Comparing is not a set difference, so this feature renders the template, pulls
the body back out of the rendered block, and compares it with the body read off
the device. Blank lines *inside* the text are content and are preserved;
whitespace at either end is not, so a device that stores it slightly
differently does not look like a change on every run.

IOS is wrapped in a `^C` delimiter and EOS terminated with `EOF`; both are the
template's business. The config push runs with netmiko's `cmd_verify` off,
because the device stops offering a prompt between the delimiters.

## ACLs

```bash
./configure.py acl --apply              # every ACL in the standards file
./configure.py acl -a SNMP-POLLERS      # just this one
```

**Order is enforced.** An ACL is an ordered list, not a set: `permit
10.1.1.0/24` before `deny any` and after it mean different things. Entries are
compared positionally, and a device whose entries differ in content *or order*
has that ACL rebuilt:

```
no ip access-list standard SNMP-POLLERS
ip access-list standard SNMP-POLLERS
 permit 10.1.1.0 0.0.0.255
 permit 10.2.0.0 0.0.255.255
 deny any log
```

The negation and the rebuild go out in the same config push. **There is a real
gap between them** -- a few milliseconds inside one session during which the
ACL does not exist, and anything referencing it behaves as that platform
behaves with a missing ACL. There is no reordering that is safer.

Whether that is acceptable depends on the ACL, so **each one decides for
itself**:

```yaml
acls:
  - name: SNMP-POLLERS
    permit: snmp.allow
    deny_log: true
    rebuild: true        # a missed poll is an acceptable cost here
```

Dropping a poller list for a moment costs a missed poll. Dropping the ACL on a
VTY line or an edge interface is a different question with a different answer,
so `rebuild` is off unless the file says otherwise. Without it:

* an ACL that is **missing** is still created -- there is nothing to delete, so
  there is no window;
* an ACL that **exists and has drifted** is reported and left alone:

```console
atl-core-sw1 (10.1.10.11) [cisco_ios] NEEDS ATTENTION
    VTY-ACCESS has drifted (1 entries on the device, 2 in the standard).
    Rewriting it means deleting it first, so it would not exist for a moment --
    set `rebuild: true` on VTY-ACCESS in the standards file if that is
    acceptable for this ACL, or fix it by hand.

summary: 1 device(s), 0 compliant, 0 changed, 1 needing attention
```

A device needing attention exits `2` whether or not `--fail-on-diff` was given:
the tool was asked to converge and deliberately did not, which is not something
to leave to an unread log.

The file states networks once, platform-neutrally, and each template writes
them the way its platform does: IOS gets wildcard masks (`10.1.1.0 0.0.0.255`,
and `host 10.1.1.5` for a /32), EOS gets prefix lengths (`10.1.1.0/24`). The
parser normalizes both back to CIDR, so the two spellings of the same rule
compare equal.

`--replace` deliberately does nothing extra here. Making a device's ACLs
exactly the file's list would delete every ACL the file does not mention --
VTY, NAT, route-map -- which is not a thing to offer as a flag. Only ACLs named
in the file are ever touched. Standard ACLs only for now; an extended ACL is
refused rather than guessed at.

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

## SNMP

```bash
./configure.py snmp --apply
```

Manages v3 users, groups, views and trap hosts, sets location/contact, and
removes v2c communities.

**SNMPv3 users are not in the running config on IOS.** `show running-config`
never shows `snmp-server user`, so this feature reads `show snmp user` as well
and takes users from whichever command produced them (EOS does write them into
the config). That is why the IOS entry in the feature table reads two commands.

`show snmp user` reports the group and the auth/privacy protocols but **never
the passphrases**, so a passphrase change cannot be detected. An existing user
is therefore rewritten when its group or its protocols differ from the
standard, and left alone when they match:

```
no snmp-server user nmsuser NMS-RO v3
snmp-server user nmsuser NMS-RO v3 auth sha <redacted> priv aes 128 <redacted> access SNMP-POLLERS
```

A passphrase change alone therefore looks like no change at all.
`--rewrite-users` negates and recreates every managed user regardless of what
the device reports, which is the way to push one.

**Passphrases** come from `$NETOPS_SNMP_AUTH_<USER>` and
`$NETOPS_SNMP_PRIV_<USER>`, or from a `--passphrase-secret` AWS secret shaped
`{"nmsuser": {"auth": "...", "priv": "..."}}`, or a prompt. They are scrubbed
from every command list, report and device echo. They are resolved for every
managed user up front, because which devices are missing which user is not
known until each one has been read -- so a run needs them even if nothing turns
out to need changing.

**`communities: []` is enforced in `--add` mode too.** "There must be none" is
a standard, not an extra to be left alone, so a community found on a device is
removed even without `--replace`. The string has to be named to remove it
(`no snmp-server community <string>`), and it is a credential, so the parser
flags it and the command is redacted everywhere it is shown:

```
no snmp-server community <redacted>
```

### An ACL per account

**The ACL goes on the group**, and each account gets its own group:

```yaml
snmp:
  acl: SNMP-POLLERS          # default for any group that does not name one

  groups:
    - name: NMS-RO
      security: priv
      read: NMS-VIEW
      acl: SNMP-NMS          # this account, only from the NMS itself
    - name: MON-RO
      security: priv
      read: NMS-VIEW
      acl: SNMP-MONITORING

  users:
    - name: nmsuser
      group: NMS-RO
    - name: monuser
      group: MON-RO

acls:
  - name: SNMP-NMS
    permit: [10.1.1.50/32]
    deny_log: true
    rebuild: true
```

```
snmp-server group NMS-RO v3 priv read NMS-VIEW access SNMP-NMS
snmp-server group MON-RO v3 priv read NMS-VIEW access SNMP-MONITORING
snmp-server user nmsuser NMS-RO v3 auth sha <redacted> priv aes 128 <redacted>
snmp-server user monuser MON-RO v3 auth sha <redacted> priv aes 128 <redacted>
```

This is the shape to prefer, because **a group is written into the running
config**: the binding is readable, so drift on it is detected like any other
field. Change an ACL in the file and the next run notices.

A `acl:` on an individual *user* is also accepted, but IOS never reports it
back -- `show snmp user` gives the group and the protocols and nothing else --
so it can be set and never verified. **A user never inherits `snmp.acl`**: a
user-level ACL overrides its group's, so a user quietly picking up the default
would defeat the restriction its group exists to impose. It gets one only by
naming it.

An ACL named in the snmp section must be defined in `acls:`, or the run stops
and says which ones are -- binding SNMP to an access list that does not exist
is worse than not binding it. If the file has no `acls:` section at all, they
are managed elsewhere and the name is taken on trust.

**Rebuilding a group rewrites the users in it.** A user names its group, and a
group that is negated and recreated leaves its members pointing at something
that briefly did not exist. So the plan negates the users first, then the
group, then writes both back:

```
no snmp-server user nmsuser NMS-RO v3
no snmp-server group NMS-RO v3 priv read NMS-VIEW access WRONG-ACL
snmp-server group NMS-RO v3 priv read NMS-VIEW access SNMP-NMS
snmp-server user nmsuser NMS-RO v3 auth sha <redacted> priv aes 128 <redacted>
```

Run `configure.py acl` before `configure.py snmp` on a device that does not
have the ACLs yet -- SNMP will happily reference one that does not exist.

### Platform differences

IOS writes the privacy protocol as two tokens (`aes 128`) and EOS as one
(`aes128`). **EOS has no `access <acl>` clause at all**, so an EOS device gets
the users, groups and views but not the poller restriction -- EOS does that
with a control-plane ACL, which this tool does not manage. Apply it separately.
The platform declares `access` as a field it cannot express, so it is never
compared there; without that the group would be rebuilt on every run forever.

Views are written before groups and groups before users, because a group naming
a view that does not exist yet is rejected, as is a user in a group that does
not exist yet.

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

## ServiceNow change records

Two halves of one flow. **The tool never approves a change.**

```bash
# 1. dry run raises a Normal change with the plan attached, and stops
./configure.py ntp --open-change

# 2. you approve it in ServiceNow, as usual

# 3. implement against it: apply, work-note, attach, close
./configure.py ntp --apply --change CHG0099999
```

```console
$ ./configure.py ntp --open-change
...
summary: 2 device(s), 0 compliant, 2 with pending changes
opened CHG0099999 in new
approve it, then: configure.py ntp --apply --change CHG0099999
```

`--open-change` is a dry-run action and refuses to be combined with `--apply`:
its whole job is to record what *would* be done so somebody can approve it. The
change is created in **New** with the exact commands as the implementation
plan, the device list, and the JSON report attached as evidence. The backout
plan states plainly what exists -- the pre-change configuration of every device
is in the attached report -- rather than pretending to be a procedure.

`--change` checks the state **before reading or writing a single device**:

```console
$ ./configure.py ntp --apply --change CHG0012345
error: CHG0012345 is in assess and cannot be implemented from there -- it needs
to reach scheduled or implement first. Approve it in ServiceNow, then re-run.
```

Afterwards it adds work notes naming every device and what happened to it,
attaches the report, and closes the change:

| Outcome | Close code |
| --- | --- |
| Everything changed or already compliant | `successful` |
| Some devices changed, some failed or could not be verified | `successful_issues` |
| Nothing succeeded | `unsuccessful` |

A partial failure is reported as what it was: closing it as wholly successful
or wholly failed would both be false. The device-level exit code is unaffected
-- a run with a failed device still exits 1.

If ServiceNow rejects the close *after* the devices are done, that is said
loudly and the run exits 1, because the change is still open and only a human
can fix that:

```console
ServiceNow: PATCH /api/sn_chg_rest/change/... failed (403): Insufficient rights
the devices are done, but CHG0012345 was not closed -- close it by hand
```

### Configuration

Static fields live in the `change:` section of the standards file; anything
other than `states`, `instance` and `verify_tls` is passed straight through as
a field on the change record:

```yaml
change:
  instance: acme
  assignment_group: Network Engineering
  category: Network
  risk: "3"
```

Credentials never go there -- `$SNOW_USER`/`$SNOW_PASS`, an OAuth
client-credentials pair in `$SNOW_CLIENT_ID`/`$SNOW_CLIENT_SECRET`, or
`--snow-secret` for AWS Secrets Manager.

Everything goes through the Change Management API (`/api/sn_chg_rest/change`)
rather than the `change_request` table, because that API validates the state
model. **The state values are instance-specific** -- the defaults are the
out-of-the-box Normal change model, and instances customise it constantly.
Check `sys_choice` for `change_request.state` on yours and override any that
differ under `change.states`.

Needs `requests` (`pip install requests`); nothing imports it unless one of the
two flags is used.

### On standard changes

None of this needs a Standard Change Template, which is why it works today. But
the recurring low-risk features -- ntp, syslog, banner, snmp -- are exactly what
those templates exist for: a standard change is pre-approved by definition, so
the approval gate disappears and the whole thing can run unattended. Proposing
one is a one-off ServiceNow admin task. Worth doing once this flow has proven
itself on a few real changes.

## Is it actually working?

Converging `ntp server 10.50.0.10` onto a device says nothing about whether the
device can *reach* it, whether the association ever came up, or whether the
clock is synchronised. `check-ntp` answers that across the fleet, read-only:

```console
$ ./configure.py check-ntp

CHECK ntp  |  4 device(s), 4 at a time
expecting: 10.50.0.10, 10.50.0.11

atl-core-sw1 (10.1.10.11) ok -- synchronised to 10.50.0.10, stratum 2, offset 0.1ms
atl-dc-leaf1 (10.1.10.12) WARN -- 10.50.0.10 missed polls (reach 177)
rdu-edge-rtr1 (10.2.10.11) NOT WORKING -- clock is not synchronised; stratum 16
    (unsynchronised); 10.50.0.10 unreachable (reach 0); 10.50.0.11 is not
    associated; no association selected as sys.peer
rdu-old-box (10.2.10.12) FAILED -- OSError: connection refused

summary: 4 device(s), 1 ok, 1 with warnings, 1 not working, 1 unreachable
```

It reads `show ntp status` and `show ntp associations` and judges:

| Verdict | When |
| --- | --- |
| **ok** | Clock synchronised, every expected server associated with full reach, one selected as sys.peer |
| **WARN** | Working, but a server has missed polls (`reach` below 377) or the selected peer's offset exceeds `--max-offset` (default 1000ms) |
| **NOT WORKING** | Clock unsynchronised, stratum 16, an expected server missing or unreachable, or nothing selected as sys.peer |
| **FAILED** | The device could not be asked at all |

`-v` lists each association. `--servers` overrides what to expect; otherwise it
comes from `ntp.servers` in the standards file. Exit codes: `0` all healthy,
`2` something is unhealthy, `1` a device was unreachable -- so it drops into a
cron drift check the same way `--fail-on-diff` does.

Two details the parser gets right, because they are easy to get wrong.
**`reach` is octal**: `377` is not three hundred and seventy-seven, it is eight
successful polls out of the last eight, and `177` means one was missed. And
**EOS prints an ntpq-style table with a `t` column that IOS does not have**, so
fields are counted from both ends -- the address first, the stratum second, and
reach/delay/offset/jitter last -- rather than by position.

A check is not a Feature: it has no template, no desired-versus-current diff and
no way to apply anything. It runs show commands and reports.

## Settings that sit at their default

**A setting at its platform default is not written to the running config.**
`logging trap informational` is IOS's default and simply does not appear, so a
plain `show running-config` cannot tell "not configured" from "configured to
the default". Read that way, the tool sets the severity, reads back, still
cannot see it, reports the device unverified and refuses to save -- and does
the same again on the next run, forever.

So the features whose values can legitimately *be* the default read
`show running-config all`, which renders them explicitly:

| Feature | Command |
| --- | --- |
| `syslog` | `show running-config all \| include ^logging` |
| `snmp-packetsize` | `show running-config all \| include ^snmp-server packetsize` |

The `| include` runs on the device, so the transfer stays small even though the
device generates more. Everything else reads a plain `show running-config`:
an NTP server, an ACL entry or a local account is never a default, so there is
nothing to make visible.

Worth knowing when adding a feature: if a value could plausibly equal the
platform default, it needs `all` or it will be pushed on every run.

**A device that rejects a command fails loudly.** `show running-config all` is
not universal, and a device that does not understand it answers with an error
*string* rather than an error -- which, parsed as state, reads as "nothing is
configured". The tool would then configure everything on the strength of it. So
show output is checked for CLI error text and the device is failed instead:

```
atl-core-sw1 (10.1.10.11) [cisco_ios] FAILED -- the device did not accept
'show running-config all | include ^logging': % Invalid input detected at '^' marker.
```

If a platform of yours cannot do `all`, change that feature's `show_command`
for it -- it is per-platform already.

## How it decides

1. Read the current state with a narrow `show ... | include` so the feature can
   only ever see -- and therefore only ever remove -- its own configuration.
   Some features ask for `all`, so that a value sitting at its platform default
   is visible rather than looking unset -- see
   [Settings that sit at their default](#settings-that-sit-at-their-default).
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

1. `netops/features/<name>.py` -- a `parse` for the show command, the CLI flags,
   a `build_desired`, and a `FEATURE = Feature(...)`.
2. `templates/cisco_ios/<name>.j2` and `templates/arista_eos/<name>.j2`.
3. Add it to `FEATURES` in [`netops/features/__init__.py`](netops/features/__init__.py).

It gets its subcommand with the same dry run, `--add`/`--replace`, standards
file, credential handling, filtering, verification, secret scrubbing and
reporting -- none of which lives in the feature.

`Feature` carries an option for each way a domain has turned out not to be a
plain set of lines. Reach for one only when the domain needs it:

| Option | For |
| --- | --- |
| `plan=` | The change is not a set difference: a rotation that always rewrites (`users`), a scalar (`snmp-packetsize`), an ordered list (`acl`), a text blob (`banner`). |
| `Desired.secrets` | Values that must never be printed. Scrubbed from commands, output and the report. |
| `Entry.data["secret_value"]` | A credential *read off the device* that has to appear in a command to be removed -- an SNMP community. |
| `not_applicable=` | The platform has no equivalent setting. Those devices are skipped and reported, not failed. |
| `PlatformSupport(ignores=...)` | A field this platform cannot express. Never compared, so it cannot cause a rebuild every run. |
| `PlatformSupport(extra_commands=...)` | State that is not in the running config -- `show snmp user`. |
| `config_options=` | netmiko keywords for the push, e.g. `cmd_verify=False` for a banner. |
| `keep_blank_lines=` | A blank line in the template is content, not layout. |

## Tests

```bash
pip install pytest
pytest
```

556 tests, no network. `tests/test_run.py` drives the real CLI, inventory,
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
- `standards.yaml` must never hold a credential:
  SNMPv3 passphrases, community strings and account passwords all come from the
  environment or AWS.
- `.env`, `inventory/hosts.csv`, `standards.yaml`, `netops-debug.log` and
  `.platform-cache.json` are gitignored. Keep real
  credentials in AWS Secrets Manager where you can.
- The debug log records device names, addresses and command output. Passwords
  are scrubbed from it the same way they are from the terminal, but treat it as
  you would any operational log.
