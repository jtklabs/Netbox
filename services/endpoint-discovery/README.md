# Endpoint discovery

An independent observation service for remote, **read-only SNMPv3** pollers.
It stores forwarding-table and IP-neighbor observations in its own PostgreSQL
database and exposes a versioned API for the network portal, NetBox integrations,
and other authorized readers. The portal is the intended primary user interface;
this directory provides its API and a server-side integration client.

```text
Remote collectors -- outbound HTTPS --> Discovery API --> Discovery PostgreSQL
Network portal    -- read-only HTTPS --> Discovery API
NetBox integration -- read-only HTTPS --> Discovery API
NetBox inventory  <-- optional GET-only inventory import -- Deployment job
```

NetBox remains authoritative for managed inventory. Discovery is authoritative
for what was observed, where, and when. Pollers do not connect to PostgreSQL or
NetBox. A NetBox outage does not interrupt searches or polling: assignments and
device/interface references are stored independently in the discovery database.
During an API outage pollers use cached assignments for up to seven days.

## Included

- BRIDGE-MIB/Q-BRIDGE-MIB learned MACs, with bridge-port-to-ifIndex resolution
  and FDB-ID-to-VLAN resolution. FDB IDs are not assumed to equal VLAN IDs.
- IPv4 ARP via the legacy table and IPv4/IPv6 neighbors via IP-MIB.
- LLDP bridge/router capability detection to identify transit ports, matching
  local port identifiers to interface names instead of assuming LLDP port
  numbers are ifIndex values. Explicit per-interface role overrides are supported.
- MAC/IP lookup with scope/VRF boundaries, known VLAN matching, freshness,
  ambiguous candidate locations, current observations, and interval history.
- Independent reader and poller identities with explicit scope allowlists.
  No wildcard scopes. Devices, contexts, and table assignments are controlled
  by the server, not by the submitted report.
- Transactional snapshots, idempotent delivery, monotonic timestamp checks,
  PostgreSQL device locks, and revision fencing for changed assignments.
- Persistent local SQLite outbox, retry across restarts, bounded queued payload
  storage, and retained quarantine for rejected reports.
- Bounded concurrent collection with independent per-device schedules, staggered
  starts, device-wide deadlines and failure backoff. API uploads and assignment
  refresh run separately from SNMP collection.
- Poller heartbeat and per-device/table health, including pending collections.
- Docker Compose and an Ansible role for Linux systemd hosts.

There are no SSH/configuration workers, device REST writers, SNMP SET paths,
or write-mode switches. Configure the device's SNMPv3 principal with a read view
and **no write view**, allowing only the assigned poller source addresses.
The collector requires SNMPv3 authPriv. Device permissions provide the final
write prohibition even if a host is compromised.

## Storage and observation semantics

Create a **separate discovery database and database login**, even if it shares
the PostgreSQL server hosting NetBox. The service never migrates NetBox tables.
The database role should own only its discovery database/schema, with no access
to NetBox. Use your normal PostgreSQL backups, connection restrictions, and
verified TLS for remote database connections.

`observations` stores one interval per device/context/table/MAC/IP/VLAN/interface
association. Unchanged polls extend `last_seen`; changes close old intervals and
open new ones. `ended_at` is when a later complete snapshot no longer contained
the association, not an exact physical disconnect time. Names and NetBox IDs are
preserved on history rows. `collections` records last attempt, last success,
status and row count. `receipts` makes delivery retries idempotent.

An error, partial mapping, unsupported table, or out-of-order report cannot
expire previous observations. A complete empty snapshot can close them. The
built-in SNMP collector conservatively reports an entirely empty table as
`unsupported`: SNMP cannot reliably distinguish no rows from a denied view.
Prior observations then become stale instead of being reported as absent.

The default stale threshold is 900 seconds; polling targets a **60-second
start-to-start interval per device**. The default is 32 collection workers per
poller and two independent upload workers. Increase the stale threshold if the
observed collection times and queue lag require it. Raw complete
walks are not retained. Daily pruning deletes observation intervals whose last
sighting is older than 90 days, including abandoned current rows, and delivery
receipts older than the seven-day replay window plus one day. These defaults
are configurable. History is bounded evidence, not a permanent audit archive.

## Deploy using Ansible

Prerequisites: Linux with systemd, Docker Engine and Compose v2 installed;
an existing PostgreSQL database; and an HTTPS reverse proxy in front of the API.
The role builds the service from this checkout on each host, installs protected
configuration, applies schema version 1, reconciles target assignments, starts
containers at boot, and schedules daily history pruning on the server.

Copy `ansible/inventory.example.ini` to your private inventory. Put settings in
private `host_vars` files and encrypt secrets using Ansible Vault or your existing
secret lookup. Inventory and host/group vars are gitignored here.

Server settings, with illustrative placeholders replaced:

```yaml
discovery_mode: server
discovery_database_url: "{{ vault_discovery_database_url }}"
discovery_principals:
  - name: network-portal
    role: reader
    token_sha256: "{{ vault_portal_token | hash('sha256') }}"
    scopes: [example/branch-1]
  - name: branch-1-poller
    role: poller
    token_sha256: "{{ vault_branch_1_token | hash('sha256') }}"
    scopes: [example/branch-1]
discovery_targets:
  - key: branch-1-access-1
    scope: example/branch-1
    poller: branch-1-poller
    address: 192.0.2.10
    name: branch-1-access-1
    credential: default
    interface_roles: {Gi1/0/48: transit}
```

Poller settings:

```yaml
discovery_mode: poller
discovery_url: https://discovery.example.internal
discovery_poller_token: "{{ vault_branch_1_token }}"
discovery_poll_seconds: 60
discovery_workers: 32
discovery_upload_workers: 2
discovery_target_refresh_seconds: 60
discovery_device_timeout_seconds: 120
discovery_snmp_credentials:
  default:
    security_name: endpoint-reader
    auth_protocol: SHA-256
    auth_passphrase: "{{ vault_snmp_auth }}"
    priv_protocol: AES
    priv_passphrase: "{{ vault_snmp_priv }}"
# Optional controller-local PEM trust bundle:
# discovery_ca_file: /secure/pki/internal-ca.pem
```

Generate separate random tokens with at least 32 characters for every identity.
Only token SHA-256 digests are deployed on the API server. Readers cannot submit
reports or fetch poller targets; pollers cannot query the portal data APIs.
Access-policy changes take effect after the API is recreated.

From this checkout:

```bash
ansible-playbook -i services/endpoint-discovery/ansible/inventory.ini \
  services/endpoint-discovery/ansible/deploy.yml --ask-vault-pass
```

The default install directory is `/opt/endpoint-discovery`. Server and poller
hosts use different Compose files; **no database credential is deployed to a
poller**. Containers run as UID/GID 10001, with a read-only root filesystem,
all capabilities dropped, and only their spool and private temporary directory
writable. Configuration is root-owned and readable by group 10001. Use separate
hosts for server and poller deployment modes.

The API binds to `127.0.0.1:8092` by default. Terminate TLS at your reverse proxy,
preserve the path, disable request-body/Authorization logging, and limit request
bodies to 16 MiB. Put a request timeout and rate limits at the proxy. Pollers
verify HTTPS certificates and reject redirects. For a subpath, strip its prefix
before forwarding, and include the prefix in `discovery_url`.

Permit poller outbound TCP 443 to the API, UDP 161 (or the configured SNMP port)
to its assigned devices, and normal DNS/NTP as required. No inbound poller port
is needed for collection. Only the API server needs database connectivity.
Ensure clocks are synchronized: reports over five minutes in the future or
older than seven days are rejected.

## Manual server deployment

From `services/endpoint-discovery`, use Python 3.14 and install
`requirements.txt` in a local virtual environment, then create private access files:

```bash
python -m discovery.bootstrap --directory config \
  --poller branch-1-poller --scope example/branch-1
cp examples/targets.json config/targets.json
cp .env.example .env
```

Bootstrap writes `access.json`, `portal.token`, and `poller.token` without
printing credentials, and refuses to overwrite existing files. Edit the target
inventory and database URL. Transfer the reader token only to the portal and
the poller token only to its poller. Remove plaintext token copies from the API
server after transferring them. For Linux bind mounts, give the container
group access to `config/access.json` and `config/targets.json`:

```bash
sudo chgrp 10001 config config/access.json config/targets.json
chmod 750 config
chmod 640 config/access.json config/targets.json
docker compose -f compose.yaml build
docker compose -f compose.yaml run --rm --no-deps manage migrate
docker compose -f compose.yaml run --rm --no-deps manage import-targets /config/targets.json --replace
docker compose -f compose.yaml up -d api
```

`DISCOVERY_DATABASE_URL` uses the `postgresql+psycopg://` SQLAlchemy URL format.
URL-encode password characters. For remote verified PostgreSQL TLS, use
`?sslmode=verify-full&sslrootcert=/config/database-ca.pem` and mount the trusted
CA at that path. This database is provisioned by your database administrator;
the role does not create or modify a PostgreSQL cluster.

Run `manage prune` daily using your scheduler; the Ansible installation includes
the systemd timer. Run `manage migrate` before each service upgrade. Schema
initialization is serialized with a PostgreSQL advisory lock and repeated
initialization is safe. Future schema versions need explicit migration code;
unknown versions fail readiness instead of being used silently.

For manual poller installation, copy the checkout, create `config/poller.token`
and `config/snmp.json` with the credentials shown above, and set `DISCOVERY_URL`
in its private `.env`. Do not copy the server `.env`. Create `spool` owned by
10001:10001, and make the poller configuration directory/files group-readable
as above. Then run:

```bash
docker compose -f compose.poller.yaml up -d --build
```

For an internal CA, mount its PEM bundle in `config` and set
`DISCOVERY_CA_FILE=/config/ca.pem`. Native poller execution additionally requires
net-snmp utilities and the shared `scripts/snmp-inventory` directory on
`PYTHONPATH`; containers and Ansible handle this automatically.

## Inventory and routing contexts

`examples/targets.json` documents the inventory format. Keys and scope identifiers
must be stable. Use a scope such as `tenant-id/site-id`; it is an authorization
boundary, not a display name. Device scopes are immutable: moving a device to
another scope requires a new key, retaining historical evidence under its
original access boundary. The target revision is generated by the server.
Buffered reports with a changed target revision are quarantined, preventing
old data from being relabeled under a new assignment or VRF.

Configure per-device SNMP contexts explicitly where VLAN/VRF contexts are needed:

```json
{"key":"vlan-10", "snmp_context":"vlan-10", "vrf":"corporate", "vlan":10, "tables":["fdb"]}
```

Use the context names your device exposes and grant them to the read-only SNMP
principal. A separate router context can collect `neighbors` for the same VRF.
VLAN 0 means unknown; it is never silently treated as VLAN 1. Do not map unrelated
routing domains to the same scope/VRF. Address overlap across scopes/VRFs is
preserved. Site scopes intentionally prevent correlations across sites; choose
scope boundaries to match both authorization and the networks being correlated.

Optional GET-only NetBox import:

```bash
# Supply NETBOX_URL and NETBOX_TOKEN_FILE to the management container using
# a private Compose override; mount the token and mapping file read-only.
python -m discovery.cli sync-netbox /secure/netbox-sites.json --replace
```

`examples/netbox-sites.json` maps explicit NetBox site IDs to scopes and pollers.
Import reads active devices with primary addresses and interface IDs. It never
writes to NetBox and only commits after every inventory request succeeds. It
does not follow pagination links to other hosts. Schedule imports independently
of endpoint polling. `--replace` disables every target missing from that import;
omit it for additive imports or when mixing manual and NetBox-managed targets.
The Ansible role treats `discovery_targets` as the complete authoritative list,
so choose Ansible inventory or scheduled NetBox import as the assignment owner.

## Portal API

All data endpoints require `Authorization: Bearer TOKEN` over HTTPS. Store the
portal's reader token on its backend, never in browser JavaScript or query
strings. Explicitly requesting an unauthorized scope returns 403; omitting a
scope searches only the principal's allowed scopes. Responses are not cached.

| Method and path | Purpose |
| --- | --- |
| `GET /healthz` | Public minimal database/schema readiness |
| `GET /v1/openapi.json` | Authenticated OpenAPI contract |
| `GET /v1/locate?ip=192.0.2.25` | Current neighbor and forwarding evidence |
| `GET /v1/locate?mac=0011.2233.4455` | MAC lookup; common MAC formats accepted |
| `GET /v1/observations` | Current or historical sightings with pagination |
| `GET /v1/collections` | Table collection health and pending work |
| `GET /v1/pollers` | Scoped poller heartbeat health |
| `GET /v1/targets` | Poller-only assignments, including revision |
| `POST /v1/reports` | Poller-only transactional collection reports |

`locate` accepts exactly one of `ip`, `mac`, or `hostname`, plus optional `scope`,
`vrf`, and `limit`. Hostname matching uses supplied neighbor hostname evidence;
the built-in SNMP collector does not perform reverse DNS. Results include
`locations`, `neighbors`, `ambiguous`, and `truncated`. Every sighting includes
timestamps, `stale`, `collection_status`, context, VLAN, scope, VRF, port role,
and optional NetBox IDs. Unknown-VLAN matches remain visibly uncertain. A
non-ambiguous result is still forwarding evidence, not proof of physical attachment.
Several endpoints may sit behind a phone, hypervisor, AP, or unmanaged switch.

`observations` accepts exact `scope`, `mac`, `ip`, `hostname`, `device_key`,
`interface`, `vrf`, and `vlan` filters. `current=false` includes closed intervals;
`since` filters `last_seen >= since` and `until` filters `first_seen <= until`.
Times are UTC Unix seconds. Use `next_after_id` as the next `after_id`, retaining
filters. Pages default to 100, maximum 1000. Pagination is not a permanent change
feed: unchanged observations update in place, and retention removes old rows.
Requery current state or use overlapping time windows for portal synchronization.
`collections` uses `next_after_key`/`after_key` with the same scope/device filters.

See [`examples/portal_client.py`](examples/portal_client.py) for a standard-library
backend client that verifies TLS, refuses redirects, and iterates history pages.
The other portal's source is needed to add its own search screen and navigation;
the discovery API itself has no dependency on that application.

## Concurrent polling and fleet sizing

Each poller maintains a due time for every assigned device. It dispatches up to
`DISCOVERY_WORKERS` devices concurrently (default 32, range 1–256), without
waiting for the slowest device to finish a fleet sweep. Contexts on one device
run serially. Devices sharing a management address and SNMP port also cannot
overlap on the same poller. Keep one poller process per assignment/spool.

`DISCOVERY_POLL_SECONDS` is now the desired **start-to-start** interval, default
60 and minimum 30 seconds. Upgrading from an explicit 300-second setting keeps
that configured interval; change it to 60 to enable the faster cadence. Initial
starts are distributed across one interval using stable device phases. If
capacity is insufficient, due devices queue fairly; they do not create an
unbounded future backlog or overlap an already running device. A poll that
exceeds its interval resumes after completion rather than spawning a catch-up
burst. Failed devices back off exponentially, capped at the greater of the
configured interval or 15 minutes, and reset their backoff after success.

All contexts and walks for a device share `DISCOVERY_DEVICE_TIMEOUT_SECONDS`
(default 120, range 1–600). The shared transport bounds each child process by
the remaining monotonic budget. It retains its previous behavior for inventory
scanner callers that do not supply a deadline. Observation ages include time
spent collecting a context, not just time since its report finished.

API traffic uses independent workers: `DISCOVERY_UPLOAD_WORKERS` (default 2,
range 1–16) sends reports over separate HTTPS connections, and assignments refresh
every `DISCOVERY_TARGET_REFRESH_SECONDS` (default 60, minimum 5). Each uploader
leases a different durable outbox row. Delivery remains ordered for each device,
so a newer snapshot cannot overtake buffered movement history. Other devices
continue uploading while one device's report is in flight or waiting to retry.
A crash leaves the row for replay after
the two-minute lease expires; server idempotency handles uncertain deliveries.
Server errors pause delivery with backoff while collection continues into the
outbox. Once its cap is reached, new collection admission pauses, completed
reports stay in a bounded memory queue, and uploads continue trying to free
space. An explicit rejected authorization pauses new collection, even if cached
assignments are available. Removed targets stop being scheduled; already running
work finishes under its old revision and remains subject to server-side checks.

The poller writes `/spool/status.json` and logs a count/timing summary every 30
seconds. It includes assigned/active/due device counts, maximum queue lag, mean
and p95 worker duration, interval overruns, failed polls, pending/quarantined
reports, spool bytes, and whether spool pressure is blocking admission. Worker
durations include local queue/storage backpressure. No addresses or credentials
are included. These are local operational metrics; the central collection API
continues to expose each table's last successful observation and freshness.

For **1,500 devices**, split assignments by reachability and site. A reasonable
starting configuration to measure is four pollers with roughly 375 devices and
32 workers each, targeting 60 seconds. This is a sizing example, not a measured
fleet guarantee. Estimate occupied collection slots with:

```text
required busy slots ≈ devices per poller × mean device collection seconds / interval seconds
```

At 375 devices and a measured 3 seconds per device, the estimate is 18.75 slots
for a 60-second interval, leaving some room within 32 workers. At 10 seconds,
it becomes 62.5 slots, requiring more workers/pollers or a longer interval.
Allow headroom for variance, multiple contexts, SNMP device load, poller CPU,
WAN latency, upload throughput and central database ingestion. Grow concurrency
gradually against representative devices. Sustained queue lag, interval
overruns or a growing upload backlog indicate insufficient capacity. Refreshing
SNMP does not bypass device MAC/ARP aging or prove that a person is present.

For example, inspect local status without exposing credentials:

```bash
docker compose -f compose.poller.yaml exec -T poller \
  python -c 'from pathlib import Path; print(Path("/spool/status.json").read_text())'
```

`--once` polls all assigned devices once in parallel, without startup staggering,
then drains delivery or returns failure with reports retained during an outage.
SIGTERM/SIGINT stop new admission and allow running workers to close. The Docker
deployment allows 150 seconds for shutdown; increase that grace if configuring
a device budget above 120 seconds. Spool data already committed remains durable.

## Operations and limits

- Reader and poller policy changes require API recreation. Rotate one identity
  at a time. Keep healthy pollers at a single replica per assignment.
- Outbox payloads are capped at 512 MiB, including quarantined reports. WAL/file
  overhead is additional. Delivery failures retain report IDs for safe replay.
  Permanent 403/409/413/422 responses quarantine evidence; inspect the local
  `reports` table (`rejected=1`) and correct the cause. Do not blindly reassign
  old reports to new scopes. Capacity exhaustion stops new collection and logs
  an error; it does not silently discard evidence. Back up/protect spool files.
- Split large fleets across pollers and measure queue lag and worker duration
  before choosing concurrency and the freshness threshold. Reports are
  bounded to 50,000 entries per table and 16 MiB per request; oversized or
  incomplete tables fail closed. The scheduler is tested with 1,500 simulated
  devices, including bounded concurrency and blocked uploads. This does not
  establish real-device throughput or central database capacity.
- Standard-MIB coverage is tested with emulated devices. Vendor-specific FDB
  contexts, MLAG/EVPN overlays, wireless client APIs, multi-hop endpoint tracing,
  automatic DNS enrichment, and full Netdisco UI parity are outside this release.
  Validate representative hardware before fleet rollout. Shared-learning FDBs
  can map to several VLANs; the collector preserves unknown VLAN instead of
  inventing an exact VLAN when the mapping is not unique.
- API/database logs omit request bodies and routine access logs. SNMP passphrases
  use the existing transport's private temporary configuration, never arguments.
  The reverse proxy and host monitoring must follow the same credential policy.

## Validation

```bash
python -m pip install -r services/endpoint-discovery/requirements-dev.txt
python -m pytest services/endpoint-discovery/tests -q
# Set TEST_DATABASE_URL to an isolated PostgreSQL test database to exercise
# the same tests there, including concurrent snapshot locking. Tests create
# and remove randomly named schemas and need permission to do that.
ruff check services/endpoint-discovery
ansible-playbook -i services/endpoint-discovery/ansible/inventory.example.ini \
  services/endpoint-discovery/ansible/deploy.yml --syntax-check
```

For the complete Linux SNMPv3 wire test and PostgreSQL integration suite:

```bash
docker build -f services/endpoint-discovery/Dockerfile -t endpoint-discovery:test .
docker build -f services/endpoint-discovery/Dockerfile.test -t endpoint-discovery:tests .
# Attach the test container to a disposable PostgreSQL test network, then:
docker run --rm --network TEST_NETWORK \
  -e TEST_DATABASE_URL=postgresql+psycopg://TEST_USER@TEST_DB/postgres endpoint-discovery:tests
```

Protocol references: [IP-MIB, RFC 4293](https://www.rfc-editor.org/rfc/rfc4293.html)
and [Q-BRIDGE-MIB, RFC 4363](https://www.rfc-editor.org/rfc/rfc4363.html).
