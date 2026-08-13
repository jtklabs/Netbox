# F5 BIG-IP tools

Tools that configure F5 BIG-IP units over iControl REST on the management
interface — no SSH access to the boxes required. Credentials come from a `.env`
file in this directory; the standards they apply come from
[`../standards.yaml`](../standards.yaml).

(`scripts/f5-image-push/` is a separate, older tool with its own `config.ini`;
new F5 tooling belongs here.)

## Setup

```bash
cd scripts/f5
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env     # fill in F5_USERNAME / F5_PASSWORD
cp devices.csv.example devices.csv         # only if you target fleets with --csv
```

Both `.env` and `devices.csv` are gitignored. Environment variables already set
in the shell override `.env`, so a CI job can `export F5_PASSWORD=...` instead of
writing the secret to a file. Point at a different file with `--env-file`.

The account needs the **Administrator** role: modifying system config is refused
with a 403 for lesser roles.

## `f5_standards.py` — apply our standards to a unit

The standards live in **`scripts/standards.yaml`**, one directory up: a
platform-neutral file holding the values that are the same for every device and
every platform — the same SNMP pollers, the same syslog collectors. That file is
committed (the standard belongs in version control, and it holds no credentials);
this tool maps it onto BIG-IP config:

| standards.yaml | BIG-IP | GUI |
|---|---|---|
| `snmp.allow` | `sys snmp allowed-addresses` | System ›› SNMP : Agent : Configuration |
| `syslog.destinations` | `sys syslog remote-servers` | System ›› Logs : Configuration |

**Nothing is written without `--commit`.** A plain run connects, compares each
unit against the file, and prints what it would add, what it would remove, and
what is already compliant:

```bash
./f5_standards.py --host 10.0.10.11                    # show the plan
./f5_standards.py --host 10.0.10.11 --commit           # add what is missing
./f5_standards.py --csv devices.csv --clean            # plan an exact match
./f5_standards.py --csv devices.csv --clean --commit   # enforce an exact match
./f5_standards.py --host 10.0.10.11 --only syslog      # one standard at a time
./f5_standards.py --host a --host b --standards /path/to/other.yaml
```

`--clean` is the difference between *adding* our standards and *enforcing* them:

| | without `--clean` | with `--clean` |
|---|---|---|
| entry the file names, missing | added | added |
| entry the file names, present | left as-is | left as-is |
| `127.0.0.0/8` (SNMP) | added if missing | kept (part of the standard) |
| any other entry on the unit | reported as `extra`, left alone | **removed** |

Both modes report the same three-way breakdown per standard, so the plan is
legible before anything is committed:

```
$ ./f5_standards.py --host 10.0.10.11 --clean
standards: /home/you/Netbox/scripts/standards.yaml
  snmp    allow 127.0.0.0/8, 10.1.1.0/24, 10.2.0.0/16
  syslog  send to 10.1.1.50:514, 10.1.1.51:1514
mode:      report only, nothing will be written, exact match (--clean)

[bigip-dc1-a] --- SNMP allow list ---
[bigip-dc1-a]   on unit:    127.0.0.0/8, 10.1.1.0/255.255.255.0, 192.168.50.0/24
[bigip-dc1-a]   compliant:  127.0.0.0/8, 10.1.1.0/255.255.255.0
[bigip-dc1-a]   to add:     10.2.0.0/16
[bigip-dc1-a]   to remove:  192.168.50.0/24
[bigip-dc1-a] --- syslog destinations ---
[bigip-dc1-a]   on unit:    10.99.99.9:514 (old-collector), 10.1.1.50:514 (primary)
[bigip-dc1-a]   compliant:  10.1.1.50:514 (primary)
[bigip-dc1-a]   to add:     10.1.1.51:1514
[bigip-dc1-a]   to remove:  10.99.99.9:514 (old-collector)

summary:
  bigip-dc1-a (10.0.10.11)
    snmp    drift      +10.2.0.0/16 -192.168.50.0/24
    syslog  drift      +10.1.1.51:1514 -10.99.99.9:514 (old-collector)
```

Behavior common to both standards:

- **Compared by meaning, not by text.** A specified `10.1.1.0/24` matches an
  existing `10.1.1.0/255.255.255.0`; a syslog destination matches on **where it
  sends** (host and port), never on the object name the unit filed it under. An
  entry that already says the right thing is never rewritten.
- **Verified.** After a commit each standard is re-read and re-compared, so a
  silent rejection, a dropped removal, or a rewrite by the unit can't pass for
  success.
- **Saved once.** REST writes only touch the running config, so a commit that
  changed anything runs the equivalent of `tmsh save sys config` — one save per
  unit, covering every standard. `--no-save` skips it (live now, lost on reboot).
- **Whole-list writes.** `PATCH` on `allowedAddresses` and on `remoteServers`
  replaces the list wholesale, so the tool always reads first and writes the full
  intended list back. That is what makes `--clean` a removal and its absence a
  preserve.
- One unreachable unit never stops a fleet, and one standard failing on a unit
  does not stop the others on that unit — each fails on its own line.
- A typo'd key in the standards file is warned about rather than silently read as
  "this standard is not defined", which would look compliant while enforcing
  nothing.

**Exit codes** — so a cron compliance check can tell "all good" from "needs
work": `0` every unit compliant (or committed successfully), `2` drift found but
not committed, `1` at least one unit or standard failed (outranks drift).

### SNMP specifics

- `127.0.0.0/8` is in the standard whether or not the file lists it — a BIG-IP
  polls its own SNMP over localhost, and every unit ships with that entry — so it
  is added when missing and never removed by `--clean`. `--no-localhost` drops it
  deliberately, and that run warns before removing it.
- Accepts CIDR (`10.1.1.0/24`), a bare address (`10.1.1.5`, sent the way BIG-IP
  writes single hosts), netmask form (`10.1.1.0/255.255.255.0`) and IPv6. A
  network with host bits set (`10.1.1.5/24`) is rejected with the two unambiguous
  alternatives rather than being silently widened.
- An entry that merely *covers* a network the file names (`10.0.0.0/8` for
  `10.1.1.0/24`) is not a match: the standard names the network, so it is added
  explicitly, and the redundancy is noted rather than done silently.
- **Communities carry their own `source`.** A v2c community can restrict source
  addresses on top of the allow list. Every run checks the unit's communities and
  warns if all of them are source-restricted such that our networks still cannot
  poll — the usual reason SNMP stays silent after the allow list looks right. It
  also notes when a unit has no v2c community at all (SNMPv3-only).

### Syslog specifics

- A bare address in the file means the default port, 514; use the `host`/`port`
  form for anything else. Hostnames are accepted — the unit resolves them.
- Changing a collector's port in the file is an add plus a remove, because a
  different port is a different destination.
- Destinations we add are named `standards-<host>-<port>` on the unit. Existing
  matching entries keep their own name and any `localIp` someone set deliberately.
- BIG-IP's `remote-servers` send **UDP only**. A TCP collector needs a raw
  syslog-ng `include`, which this tool does not manage — hence no protocol key in
  the standards file.
- BIG-IP sends every log message to every remote server, so without `--clean` an
  extra destination keeps receiving a copy. The run says so.

### Per-unit, not synced

BIG-IP does not ConfigSync system SNMP or syslog settings, so an HA pair needs
the tool run against *both* members (`--host` twice, or list both in the CSV).
The tool prints a reminder whenever it changes anything.

## Adding another standard, or another tool

A new **standard** goes into `f5_standards.py` as another planner registered in
`SECTIONS`: read the unit, return a `Plan` (what is compliant, what to add, what
is extra, and the payload to `PATCH`), and the shared driver handles rendering,
`--clean`, `--commit`, verification and the save. Both current standards are
keyed lists, which is why they share one `Plan`; a scalar standard (a banner,
say) will need its own shape.

`f5common.py` holds everything reusable across tools: `.env` loading
(`load_settings`), the standards file (`load_standards`, `Destination`), inventory
(`load_devices`, `Device`), the token-authenticated REST client (`F5Client`,
including `save_config()`), timestamped `log()`, readable failures (`error_text`),
and address parsing (`normalize_network`, `parse_address_spec`, `covers`).

A new **tool** is a thin CLI over that:

```python
from f5common import F5Client, Device, load_settings

settings = load_settings(args.env_file)
with F5Client(Device(host=args.host, name=args.host), settings) as client:
    print(client.get_json("/mgmt/tm/sys/syslog"))
```

`F5Client` as a context manager logs in on entry and always deletes its auth
token on exit. Failures are raised as `RuntimeError` carrying the message F5 put
in the response body, which is where the real reason lives (`requests`' own error
string drops it).

`load_standards()` lives in `f5common.py` because these tools are its only reader
today; when a second platform's tooling arrives, it belongs in a module both can
import.
