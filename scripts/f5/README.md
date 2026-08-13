# F5 BIG-IP tools

Tools that configure F5 BIG-IP units over iControl REST on the management
interface — no SSH access to the boxes required. Credentials come from a `.env`
file in this directory, shared by every tool here.

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

This is where our BIG-IP standards get deployed. **Today it covers one standard:
SNMP access** — which networks may poll the unit (`sys snmp allowed-addresses`,
the GUI's Client Allow List under **System ›› SNMP : Agent : Configuration**).
Logging, banner and the rest join it as further sections in this script.

**Nothing is written without `--commit`.** A plain run connects, compares each
unit against the networks you passed, and prints what it would add, what it would
remove, and what is already compliant:

```bash
./f5_standards.py 10.1.1.0/24 --host 10.0.10.11                    # show the plan
./f5_standards.py 10.1.1.0/24 --host 10.0.10.11 --commit           # add what is missing
./f5_standards.py 10.1.1.0/24 --csv devices.csv --clean            # plan an exact match
./f5_standards.py 10.1.1.0/24 --csv devices.csv --clean --commit   # enforce an exact match
./f5_standards.py 10.1.1.0/24 10.2.0.0/16 --host 10.0.10.11 --host 10.0.10.12
```

`--clean` is the difference between *adding* our standard and *enforcing* it:

| | without `--clean` | with `--clean` |
|---|---|---|
| specified network missing | added | added |
| specified network present | left as-is | left as-is |
| any other entry on the unit | reported as `extra`, left alone | **removed** |

Both modes report the same three-way breakdown, so the plan is legible before
anything is committed:

```
[bigip-dc1-a] allow list now: 127.0.0.0/8, 10.1.1.0/255.255.255.0, 192.168.50.0/24
[bigip-dc1-a] compliant:      10.1.1.0/255.255.255.0
[bigip-dc1-a] to add:         10.2.0.0/16
[bigip-dc1-a] to remove:      127.0.0.0/8, 192.168.50.0/24
[bigip-dc1-a] note: removing 127.0.0.0/8 closes SNMP over localhost, which the
              unit's own internal monitoring uses — pass 127.0.0.0/8 as an
              argument to keep it
[bigip-dc1-a] not committed (no --commit) — nothing was written
```

Behavior:

- **Compliance is by meaning, not by text.** A specified `10.1.1.0/24` matches an
  existing `10.1.1.0/255.255.255.0` and is left alone — enforcing a standard
  never rewrites an entry that already says the right thing. An entry that merely
  *covers* a specified network (`10.0.0.0/8` for `10.1.1.0/24`) is not a match:
  the standard names the network, so it is added explicitly, and the redundancy
  is noted rather than done silently.
- **Verified.** After a commit the list is re-read and re-compared, so a silent
  rejection, a dropped removal, or a rewrite by the unit (`10.1.1.5/32` →
  `10.1.1.5`) can't pass for success.
- **Saved.** REST writes only touch the running config, so a commit then runs the
  equivalent of `tmsh save sys config`. `--no-save` skips that — live now, lost
  at the next reboot.
- **Whole-list write.** `PATCH` on `allowedAddresses` replaces the list wholesale,
  so the tool always reads first and writes the full intended list back. That is
  what makes `--clean` a removal and its absence a preserve.
- Accepts CIDR (`10.1.1.0/24`), a bare address (`10.1.1.5`, sent the way BIG-IP
  writes single hosts), netmask form (`10.1.1.0/255.255.255.0`) and IPv6.
  A network with host bits set (`10.1.1.5/24`) is rejected with the two
  unambiguous alternatives rather than being silently widened. At least one
  network is always required, so `--clean` can never empty the list and close
  SNMP completely.
- One unreachable unit never stops a fleet: it fails on its own line and the run
  continues.

**Exit codes** — so a cron compliance check can tell "all good" from "needs
work": `0` every unit compliant (or committed successfully), `2` drift found but
not committed, `1` at least one unit failed (outranks drift).

### Two things that also gate SNMP access

- **SNMP config is per-unit.** BIG-IP does not ConfigSync system SNMP settings,
  so an HA pair needs the tool run against *both* members (`--host` twice, or
  list both in the CSV). The tool prints a reminder whenever it changes anything.
- **Communities carry their own `source`.** A v2c community can restrict source
  addresses on top of the allow list. Every run checks the unit's communities and
  warns if all of them are source-restricted such that the specified networks
  still cannot poll — the usual reason SNMP stays silent after the allow list
  looks right. It also notes when a unit has no v2c community at all
  (SNMPv3-only).

## Adding another standard, or another tool

`f5common.py` holds everything reusable: `.env` loading (`load_settings`),
inventory (`load_devices`, `Device`), the token-authenticated REST client
(`F5Client`, including `save_config()`), timestamped `log()`, readable failures
(`error_text`), and allow-list address parsing (`normalize_network`,
`parse_address_spec`, `covers`) that any other allow list — `sys sshd allow`,
`sys httpd allow` — needs in the same shape.

A new **standard** goes into `f5_standards.py` beside the SNMP section: a
function that reads the unit, builds a plan of add/remove/compliant, renders it,
and only writes when `commit` is true. Keeping that shape is what lets one run
report on everything and commit all of it together.

A new **tool** is a thin CLI over `f5common`:

```python
from f5common import F5Client, Device, load_devices, load_settings, log

settings = load_settings(args.env_file)
with F5Client(Device(host=args.host, name=args.host), settings) as client:
    print(client.get_json("/mgmt/tm/sys/snmp"))
```

`F5Client` as a context manager logs in on entry and always deletes its auth
token on exit. Failures are raised as `RuntimeError` carrying the message F5 put
in the response body, which is where the real reason lives (`requests`' own error
string drops it).
