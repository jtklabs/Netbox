# Remote collectors

A collector is a small box at a site that can reach devices the central NetBox
server cannot. It runs one orb-agent container, scans locally, and pushes
results **outbound** to the central Diode endpoint, which applies them to NetBox.

What a collector needs: Docker, and outbound reach to the Diode endpoint. That
is all. It never touches PostgreSQL, Redis, or any NetBox credential — the only
secret it holds is its own scoped ingest credential, revocable on its own.

## Standing one up

**On the central server**, mint credentials for it:

```bash
./scripts/new-collector.sh site-branch-a
```

That prints a `client_id`/`client_secret` pair scoped to `diode:ingest` only.
The secret is shown once.

**On the remote box**, copy this `collector/` directory across, then:

```bash
./install.sh                 # creates collector.env, tells you to fill it in
vi collector.env             # paste the credentials + set DIODE_TARGET
vi policies.yaml             # describe what this site scans
./install.sh                 # validates and starts
```

`./install.sh --check` validates config and Diode connectivity without starting
anything. Re-running is safe.

Managing it afterwards:

```bash
docker compose logs -f orb-agent   # scan results and ingest confirmations
docker compose restart orb-agent   # rescan now (unscheduled policies run at start)
docker compose down                # stop
```

Revoke a collector centrally at any time:

```bash
./scripts/new-collector.sh --list
./scripts/new-collector.sh --revoke <client_id>
```

## Reaching Diode from a remote site

The central Diode endpoint is bound to loopback by default, so remote
collectors cannot reach it as shipped. Two options:

**A tunnel (recommended).** WireGuard/site-to-site VPN, then point
`DIODE_TARGET` at the central box's tunnel address:
`grpc://10.90.0.1:8090/diode`. Nothing else to configure, and the ingest
endpoint stays off the public internet.

**Public TLS.** Add a TLS server block to the Diode nginx and publish it, then
use `grpcs://diode.example.com:8443/diode`. The cert's SAN must match the
hostname collectors use — verification is strict. For a private CA, set
`DIODE_CERT_FILE` in `collector.env` to a path under `/opt/orb/`.

Do not expose the endpoint as plaintext `grpc://` on the public internet: the
OAuth bearer token is the only thing protecting it, and tokens are replayable
for their one-hour lifetime.

## Fleet notes

- **Stagger the schedules.** Nothing throttles simultaneous arrivals at Diode,
  and the reconciler's write rate to NetBox (`DIODE_TO_NETBOX_RATE_LIMITER_RPS`,
  default 20, concurrency 1) is shared by the whole fleet. Use a different
  minute per site, and raise those limits centrally before adding many
  collectors.
- **`agent_name` is your provenance.** It is set from `COLLECTOR_NAME` and
  stamped on everything that collector ingests. Keep it unique per box.
- **Site defaults are per policy.** `config.defaults.site` decides which NetBox
  Site the discovered gear lands in, so each collector's `policies.yaml` sets
  its own.
- **Outgrowing per-box policy files?** orb-agent can pull policies from a Git
  repo instead: set `config_manager.active: git` with a repo URL and branch, and
  put a `selector.yaml` at the repo root mapping agent labels to policy files.
  Each collector then fetches only the policies matching its `labels:`, and
  changes hot-reload on a cron poll without restarting agents. That is the point
  to switch at — roughly a handful of sites.

## Running your own Python on a collector

`worker-example/` is a working skeleton. orb-agent's `worker` backend loads a
pip-installed package of yours and calls `run()` on a schedule; whatever
entities you return are shipped through Diode into NetBox.

```bash
cp workers.txt.example workers.txt      # lists ./worker-example
# in collector.env:
#   INSTALL_WORKERS_PATH=/opt/orb/workers.txt
# in agent.yaml.template: uncomment the `worker:` backend
# in policies.yaml: uncomment the worker policy block
./install.sh
```

Inside `run()` you have ordinary Python with the remote box's network reach —
SSH to devices, call a vendor API, parse a local file, whatever. The constraint
is the **output**: entities must be NetBox-shaped (Device, Interface,
IPAddress, and ~90 other types). Data with no matching NetBox field goes into
`custom_fields`, which has a `json=` variant for arbitrary structure.

Two caveats: custom packages run **unsandboxed** inside the agent container, so
treat them as trusted code and pin versions in `workers.txt`; and installs run
at container start, so the box needs PyPI egress unless you vendor tarballs.

**If you need general remote execution** — backups, arbitrary commands, things
that are not "collect data into NetBox" — this is the wrong tool. orb-agent has
no task-dispatch capability, and the worker backend's output must be Diode
entities. Use Ansible/SSH/Salt for that.

### Why not remote NetBox workers?

Running a NetBox RQ worker at each site is technically possible and would let
NetBox custom scripts execute remotely, but it is a bad trade here. Each remote
box would need direct PostgreSQL access, both Redis databases, the shared
`SECRET_KEY` and the API token pepper — effectively the full central
credentials. NetBox's own documentation warns that anyone with write access to
the tasks Redis database can execute arbitrary code on a worker, so a
compromised site becomes central RCE. Per-script queue selection was also
explicitly rejected upstream, so routing a specific script to a specific site
is not supported.

The collector model inverts that trust direction: outbound-only, one narrowly
scoped credential per site, independently revocable, and nothing central is
exposed.
