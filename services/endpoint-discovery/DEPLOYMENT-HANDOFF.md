# Endpoint discovery deployment handoff

## Task for the implementing agent

Deploy the independent endpoint discovery service from `jtklabs/Netbox` using
our Ansible standards. Use the existing role under
`services/endpoint-discovery/ansible/roles/endpoint_discovery` as the starting
point. Deliver a reproducible playbook, private inventory layout, secret lookup
configuration, and operator run instructions. This document describes the
deployment contract; environment-specific inputs below must be supplied through
inventory or the existing secrets system.

Provision **one new database on the existing RDS PostgreSQL instance**, following
[RDS-SETUP.md](RDS-SETUP.md). Keep NetBox inventory and discovery observations in
their respective databases. The network portal reads discovery's versioned HTTP
API using its own reader identity. It does not need a database login.

The collector uses only SNMPv3 authPriv reads. Device administrators must give
its account a read view and no write view, restricted to assigned poller source
addresses. Do not add SSH, SNMP SET, device configuration, or other network write
capabilities. Writing observations into the discovery database is required.

## Existing implementation to reuse

Paths below are relative to `services/endpoint-discovery/` unless stated otherwise.

| Path | Purpose |
| --- | --- |
| `ansible/deploy.yml`, `ansible/inventory.example.ini` | Entry point and server/poller host modes |
| `ansible/roles/endpoint_discovery/` | Defaults, protected config, image build, migration, inventory reconciliation, systemd and daily pruning |
| `compose.yaml` | API and one-shot management commands; PostgreSQL connection stays here |
| `compose.poller.yaml` | Outbound poller with durable local spool; no database access |
| `Dockerfile`, `requirements.txt` | Runtime image, UID/GID 10001, pinned Python dependencies |
| `examples/targets.json`, `examples/netbox-sites.json` | Assignment and optional GET-only NetBox import formats |
| `examples/portal_client.py`, `README.md` | Portal integration example and full API/configuration reference |

The role copies a repository-shaped subset onto each host and builds the image
there. Its controller checkout must include
`scripts/snmp-inventory/snmpinv/{__init__.py,snmp.py}` from the repository root.
Check out a reviewed commit/tag before running; set `discovery_repo_root` to its
absolute path if the playbook moves into another repository. Do not copy just
the service directory and omit that shared transport.

Docker Engine, Compose v2, Linux systemd, the HTTPS reverse proxy/certificate,
RDS database, and network rules are prerequisites; the existing role does not
provision them. Add those to the organization's infrastructure roles or document
the prerequisite playbook. The current role builds `endpoint-discovery:local`;
using a registry requires adapting its image inspection/build tasks and passing
an immutable image reference through both Compose environments. Do not promise
registry-based deployment without implementing that change.

## Inputs to obtain and record

| Input | Where it belongs |
| --- | --- |
| Reviewed service release and target Linux distribution | Deployment configuration |
| API host, API DNS name, proxy location, certificate and issuing CA | Server inventory and proxy role |
| Existing RDS PostgreSQL DNS endpoint, port, engine major version, new database/login names | Database provisioning inventory |
| Provisioning administrator credential | Temporary controller/bastion secret lookup only |
| Discovery database password and complete SQLAlchemy URL | Server-only secret lookup |
| RDS CA bundle file on the controller | `discovery_database_ca_file`, server only |
| Poller hosts, reachable device groups, stable identity names and explicit scopes | Poller inventory and server access policy |
| Separate random bearer token per poller and portal client | Secret lookup, generated once and retained |
| SNMPv3 authPriv profiles for each poller's devices | That poller's secret lookup only |
| Private API CA, if applicable | `discovery_ca_file` on pollers |
| Authoritative target source: Ansible inventory or scheduled NetBox sync | One selected reconciliation owner |
| History retention, freshness threshold, RDS capacity budget and alert destinations | Operations configuration |

Store secrets in Ansible Vault or the organization's secret manager; never in
Git, command arguments, task output, diffs, container build layers, or facts
caches. Use `no_log: true` and `diff: false` for added secret-bearing tasks. Do not
regenerate passwords/tokens on every run. Strip trailing newlines from tokens
before hashing; hash exactly the value that the poller/portal presents.
`discovery.bootstrap` can generate one poller and one reader locally, but a
multi-poller deployment needs a persistent secret per identity.

## Host layout and configuration

Use separate server and poller hosts, even if a site has only one poller.
The default install root is `/opt/endpoint-discovery`; runtime files are beneath
`services/endpoint-discovery/`. Retain the existing protections:

- `.env`: root-owned, mode `0600`; includes the database URL only on the server.
- `config/`: root:10001, mode `0750`; JSON/token/CA files root:10001, mode `0640`.
- `spool/`: 10001:10001, mode `0700`; persistent across image/config upgrades.
- Containers: UID/GID 10001, read-only root, `/tmp` tmpfs, no capabilities.
- Management commands: use the server Compose `manage` service, with the same
  environment, mounted CA, and application image as the API.

Follow the full server/poller examples in [README.md](README.md#deploy-using-ansible).
These additional server variables connect the existing RDS instance:

```yaml
discovery_mode: server
discovery_repo_root: /srv/releases/Netbox
discovery_database_url: "{{ vault_discovery_database_url }}"
discovery_database_ca_file: /secure/pki/rds-ca-bundle.pem
discovery_bind_address: 127.0.0.1
discovery_public_port: 8092
discovery_stale_seconds: 900
discovery_retention_days: 90
```

The protected URL uses this form (encode the password as a URL component):

```text
postgresql+psycopg://endpoint_discovery:ENCODED_PASSWORD@EXISTING_RDS_ENDPOINT:5432/endpoint_discovery?sslmode=verify-full&sslrootcert=/config/database-ca.pem
```

`discovery_database_ca_file` copies the controller's RDS trust bundle to the
server's `config/database-ca.pem`, already mounted at `/config/database-ca.pem`
in both server containers. It is separate from `discovery_ca_file`, which trusts
the poller-to-API HTTPS certificate. Never disable certificate verification to
work around a missing file or hostname mismatch.

For each poller, begin with these concurrency settings:

```yaml
discovery_mode: poller
discovery_url: https://discovery.example.internal
discovery_poller_token: "{{ vault_this_poller_token }}"
discovery_poll_seconds: 60
discovery_workers: 32
discovery_upload_workers: 2
discovery_target_refresh_seconds: 60
discovery_device_timeout_seconds: 120
# Only when the API uses a private CA:
# discovery_ca_file: /secure/pki/internal-api-ca.pem
# Also supply discovery_snmp_credentials as shown in README.md.
```

Set the server principal's `name` to the target's `poller` value and permit that
target's scope explicitly in its `scopes` list. These are application identities,
not automatically derived from Ansible hostnames. Scopes do not support `*`.
Use a separate `role: reader` principal for the portal, with only required scopes.
Only SHA-256 token digests belong in server `discovery_principals`; plaintext
poller tokens go to their respective pollers and reader tokens to portal backends.
Access-policy changes require API recreation/restart, handled by the role.

## Assignment ownership

The existing role runs `import-targets /config/targets.json --replace` on every
server deployment. `discovery_targets` is therefore the complete authoritative
inventory; an empty list disables all existing targets. Populate and validate
the list before rollout. Stable keys prevent accidental history fragmentation.
Assign each device to one poller, with routing reachability, credential profile,
scope, optional NetBox IDs, and explicit VRF/SNMP contexts as needed. Mark known
uplinks/transit ports to improve endpoint location interpretation.

If scheduled NetBox synchronization is selected instead, adapt the role to skip
its target import and install a separate controlled sync job. Use
`manage sync-netbox /config/netbox-sites.json --replace` with a GET-only NetBox
token, `NETBOX_URL`, `NETBOX_TOKEN_FILE`, and optional `NETBOX_CA_FILE`. Mount the
mapping/token/CA and pass those variables to the management container explicitly;
the shipped Compose file does not pass them through. The importer is a one-shot
command, not an automatically installed periodic service. Keep this credential
on the server/import host, never a remote poller. Do not run both assignment
owners with `--replace`.

## Network and proxy contract

| Source | Destination | Required access |
| --- | --- | --- |
| Ansible controller | Managed Linux hosts | Existing management transport, usually SSH |
| API/management host | Existing RDS endpoint | Private TCP 5432, restricted by source security group |
| Remote pollers | API HTTPS endpoint | Outbound TCP 443; no incoming collection port |
| Each poller | Its assigned devices | UDP 161 or explicitly configured SNMP port, including return traffic |
| Portal backend | API HTTPS endpoint | TCP 443 with reader bearer token |
| Hosts | Organization DNS, NTP, image/package sources | Existing approved infrastructure access |

Do not grant poller security groups RDS access. Device ACLs must match actual
poller egress addresses. For firewalls/NACLs, account for response traffic.
Configure DNS and time synchronization before starting containers.

The API defaults to loopback port 8092. Put an HTTPS reverse proxy in front of
it, preserve `Authorization` and API paths, disable body/token logging, allow
16 MiB request bodies, and set a finite upstream timeout (start with 60 seconds,
then measure). Apply rate limits that accommodate concurrent report uploads.
If the proxy is on another host, bind to the required private interface and
restrict 8092 to that proxy; loopback cannot be reached remotely. A containerized
proxy also needs an explicit host/network route. Pollers reject redirects, so
configure their final HTTPS URL directly. For a URL subpath, strip the prefix at
the proxy before forwarding. Check HTTPS readiness from a poller network, not
only loopback readiness on the server.

## Playbook order and idempotence

1. Validate inventory, scopes, unique tokens, nonempty intended assignments,
   reachable devices, release pin and required secrets without printing secrets.
2. Install the OS/Docker/Compose prerequisites and network/proxy infrastructure.
   If hosts cannot build using outbound registries/package sources, implement
   the organization's image delivery path before deployment.
3. Provision the new logical RDS database/login/schema using the separate
   restricted provisioning phase in [RDS-SETUP.md](RDS-SETUP.md). Run once from
   the controller/bastion with database connectivity, not on every poller.
4. Deploy server sources, protected configuration and database CA. Build the
   image, run `manage migrate`, then reconcile the selected target source.
5. Start/enable `endpoint-discovery.service` and the daily
   `endpoint-discovery-prune.timer`. Check `/healthz` locally and via HTTPS.
6. Deploy one pilot poller; verify real collection and portal queries. Deploy
   the remaining pollers in batches after that passes.
7. Record release, host assignments, secret references, acceptance evidence,
   rollback procedure, and operations ownership in the deployment repository.

Database creation must run outside a transaction. Query catalogs or use
idempotent database modules before creation; an existing database/role with the
wrong owner or unexpected attributes must fail for review instead of being
dropped or silently adopted. Never drop/recreate the database on normal runs.
Keep administrator credentials out of the service environment. Repeated app
migrations are safe for the current schema version and take a PostgreSQL
advisory lock. Do not run a fleet-wide migration once per poller.

The supplied role uses imperative image/management commands and is not a full
check-mode implementation. Do not treat `--check` success as a deployment test.
On rerun, secrets remain stable, source builds happen only for changed sources
or missing images, and the spool/history survive. Verify the actual rerun and
reboot behavior on a disposable host before production.

## Capacity and acceptance checks

Start planning with four pollers and roughly 375 devices each, adjusted for
network boundaries. At 32 workers and 60-second intervals, a poller has at most
1,920 worker-seconds per interval before overhead. At a three-second mean per
device that needs about 19 workers; at ten seconds it needs about 63. Those are
planning examples, not measured fleet capacity. Increase poller count or worker
capacity only after checking device, host, WAN, and RDS load. Scheduling is
concurrent and staggered; a device is never polled concurrently with itself.

Use the README's [capacity and freshness guidance](README.md#concurrent-polling-and-fleet-sizing) and check:

- Initial and repeated `/healthz` return 200; migrations and schema stay in the
  discovery database. Verify RDS TLS and role attributes as in the DB runbook.
- Correct poller token fetches only assigned targets. A reader cannot upload;
  a poller cannot use reader endpoints; wrong scopes are rejected.
- Collect a known endpoint through SNMPv3, then query it using the actual portal
  backend and reader token via `/v1/locate` or `/v1/observations`.
- Observe multiple polling intervals, worker counts, p95 scheduling lag,
  device-budget overruns, successful collection times, and upload backlog in
  poller logs/`spool/status.json`. A 60-second setting is a target, not a guarantee.
- Disconnect API access briefly: collection continues into the bounded spool;
  reconnection drains it without duplicate history. Preserve the spool on
  restarts. Alert on growing backlog, quarantined reports and stale pollers.
- Restart/reboot server and pilot poller; services recover and assignments,
  history and pending reports persist. A second playbook run retains secrets
  and does not disable unrelated intended targets.
- Device write attempts are denied by the SNMP account's permissions; evaluate
  the configured read/write views without issuing disruptive write operations.
- Watch shared RDS CPU, memory, storage, I/O, connections, query latency and
  NetBox performance during each rollout batch. Confirm prune timer execution
  and the agreed backup/restore procedure.

Poll timing cannot eliminate switch MAC/ARP cache aging. Report last-seen and
stale status in the portal; do not label an absent/stale entry as an exact user
disconnect. The default freshness threshold is 900 seconds and history is 90
days; adjust based on the pilot and retention requirements.

## Upgrade, rollback, and recovery

Pin the release in the deployment repository. Back up before a schema-changing
upgrade, migrate once, then roll pollers in batches. Rotate bearer tokens,
database passwords and CA bundles through protected configuration with explicit
service recreation. This version does not refresh IAM database auth tokens;
use the dedicated password login unless token refresh support is implemented.

For a failed rollout, stop the affected service and restore the prior reviewed
image/config only if its schema compatibility is confirmed. Keep poller spools
and the discovery database. Do not run `down -v`, delete spool files, or restore
the shared production RDS instance as an application rollback. Use a separate
restored instance and a database-scoped recovery plan from the DB runbook when
data recovery is required.
