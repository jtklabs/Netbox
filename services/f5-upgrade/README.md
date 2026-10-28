# F5 SSH upgrade service

A standalone implementation for BIG-IP upgrades controlled through a versioned
HTTP API, CLI, or web console. **Every upgrade operation uses SSH/SFTP and tmsh;
there are no iControl REST calls.** It has no dependency on NetBox or the older
F5 scripts in this repository.

The server stores jobs, events, pre/post reports, worker ownership, and original
boot volumes in SQLite. UCS archives live in a separate local backup directory.
Multiple worker processes or containers share these directories on **one host**.
Upgrades run concurrently; one device or configured HA group has only one running
upgrade job. SNMP v3 walks use UDP and share the durable worker queue. SQLite WAL must use a local filesystem, not NFS/SMB or multi-host replicas.

## Upgrade job controls

All upgrade JSON options have corresponding CLI flags (`_` becomes `-`). Boolean CLI
flags also support `--no-...`. Use `POST /api/v1/plan` or `f5-upgrade plan` to see
inherited steps without connecting to a device. Jobs default to a read-only
pre/post validation; **install and reboot are opt-in**.

| Option | Behavior |
| --- | --- |
| `device` | ID in the server's device inventory; no arbitrary hosts or commands in jobs |
| `transfer` | Copy ordered images from `F5_IMAGE_ROOT` to `/shared/images` over SFTP; verify SHA-256 |
| `images` | Ordered array of `{name, sha256, version, build, kind}`; kind is `image` or `hotfix` |
| `images_on_device` | Skip inherited upload for install; still verify remote checksum and metadata |
| `create_ucs` | Save configuration, create UCS, download it and verify size and SHA-256 |
| `ucs_unencrypted` | Implies UCS creation; omit the UCS encryption passphrase, retaining private keys |
| `backup_directory` | Implies UCS creation; relative directory under `F5_BACKUP_ROOT`; defaults to device ID |
| `install` | Implies UCS backup and transfer (unless images are already on device), then installs images in order |
| `target_volume` | Explicit inactive boot volume, such as `HD1.2`; creates it if needed |
| `allowed_source_versions` | Exact running-version allowlist, required for install/reboot; no wildcards |
| `expected_version`, `expected_build` | Required for reboot of an already staged volume; inherited from the final image for install |
| `reboot` | Reboot to the target and verify a new boot identity, active volume, version, and build |
| `drain` | Requires reboot; temporarily disable currently enabled **virtual addresses** in all partitions |
| `drain_connections` | Implies drain; reboot once total current clientside virtual-server connections are at or below this count |
| `drain_percent` | Implies drain; percentage to **remove** from the initial count; mutually exclusive with count |
| `drain_timeout` | Maximum wait after disabling addresses, seconds; default 300 |
| `reboot_on_drain_timeout` | Explicitly reboot when drain times out; default false (restore addresses and stop) |
| `install_timeout` | Per-image installation wait, seconds; default 3600 |
| `reboot_timeout` | Wait for verified new boot, seconds; default 900 |
| `validation_timeout` | Let monitors settle before reporting health differences, seconds; default 180 |

For example, 1,000 initial connections with `drain_percent: 90` means a threshold
of 100 remaining. A zero baseline produces a zero threshold. With no threshold,
drain defaults to zero connections. Counts sum `clientside.cur-conns` across all
virtual servers; these are clientside LTM flows, not a full system connection-table
count or a guarantee about stateless traffic.

Draining runs `modify /ltm virtual-address <address> enabled no`; it never disables
virtual servers. It records all original address states and each mutation intent
before sending commands. Previously disabled addresses remain disabled. Address
changes are not saved or synced; the configuration is saved before installation
and before draining. On a verified reboot, changed addresses are restored. On a
pre-reboot error, restoration is attempted immediately. An uncertain reboot or
lost worker leaves explicit recovery information instead of issuing blind cleanup.
The drain method rejects automatic config sync, which could propagate address
disables to peers. Avoid other config saves/syncs during the job.

An installation stage whose target already has the exact requested version/build
is recorded as `install_already_complete` and skipped. It does not refresh the
configuration on that previously staged volume; review post-validation when booting it.

Encrypted UCS is the default. Set the device's `ucs_passphrase_env` on every
worker. The passphrase travels through SSH stdin, not a remote process argument,
and is never put in the job database or event log. Unencrypted UCS is explicitly
requested with `ucs_unencrypted`; it is not `no-private-key`. Downloaded backups
have private permissions and UUID filenames. The server never accepts a client
supplied absolute backup path. Mount an alternate backup root if necessary.

## Docker

From this directory:

```bash
cp .env.example .env
mkdir -p config/ssh images
cp devices.example.json config/devices.json
chmod 600 .env
# Fill in .env and config/devices.json. Place keys and a verified known_hosts
# file in config/. Container UID 10001 must be able to read them.
# Copy approved image files into images/.
docker compose up -d --build --scale worker=3
```

Open `http://127.0.0.1:8080/` for the web console, `/docs` for interactive OpenAPI
documentation, and `/openapi.json` for the integration schema. Use the bearer token
from `.env`. The console stores it only in memory for the current tab. Use an HTTPS
reverse proxy for remote API clients; change `F5_BIND_ADDRESS` only as needed.
The health endpoint reports API availability, not worker/device readiness.

Use verified SSH host keys, including `[host]:port` syntax for nonstandard ports.
Unknown or changed host keys are rejected. SSH profiles support `key_file`, optional
`key_passphrase_env`, or `password_env`. Inventory contains secret environment
variable names, never the passwords themselves. The account needs a bash login
shell, tmsh administrator rights, SFTP, and access to images and UCS files. Root
or an equivalently authorized operational account is needed; no implicit sudo
or restricted-tmsh-shell bypass is attempted.

Persist both named volumes (`data`, `backups`). Do not run `docker compose down -v`
when you need retained jobs or rollback records. Containers share `:z` bind mounts
for SELinux. Match the files' read permissions to UID 10001 without making private
keys world-readable. The image uses Python 3.12 and runs without root privileges.

## Ubuntu 24.04 / Red Hat Enterprise Linux 9

Python 3.11 or newer is required. Ubuntu 24.04 supplies Python 3.12; on RHEL 9,
install the Python 3.11 package suite (RHEL 9.2+) rather than its default Python 3.9. See the [Red Hat Python installation guide](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/installing_and_using_dynamic_programming_languages/index).
Install this directory at `/opt/f5-upgrade`:

```bash
# Ubuntu:
sudo apt-get install python3-venv python3-pip
# RHEL 9 alternative:
# sudo dnf install python3.11 python3.11-pip

cd /opt/f5-upgrade
python3 -m venv .venv          # use python3.11 on RHEL
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps .
sudo useradd --system --home /var/lib/f5-upgrade --shell /usr/sbin/nologin f5upgrade
sudo install -d -o f5upgrade -g f5upgrade -m 700 /var/lib/f5-upgrade /var/backups/f5-upgrade
sudo install -d -m 750 -o root -g f5upgrade /etc/f5-upgrade /srv/f5-images
sudo install -m 600 deploy/service.env.example /etc/f5-upgrade/service.env
# Populate service.env, devices.json, known_hosts and SSH key files.
# Native inventory key_file paths must point to /etc/f5-upgrade/..., not /config/...
# Make inventory/host keys/private keys readable by the f5upgrade account.
sudo install -m 644 deploy/f5-upgrade-api.service deploy/f5-upgrade-worker@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now f5-upgrade-api f5-upgrade-worker@1 f5-upgrade-worker@2
```

`systemd` reads the root-only environment file. Interactive commands load `.env`
(or `--env-file PATH`); existing environment variables win. Both API and workers
must use the same inventory, database, image root and backup root. Put both members
of a pair in the same `lock_group`, and use one canonical inventory entry for each
appliance. Do not edit inventory identity/address/lock groups while jobs exist in
the queue or are running; cancel pending jobs and complete/reconcile active jobs
first. The scheduler does not discover that two different DNS aliases are one F5.

The worker finishes its current job on SIGTERM before exiting. If forcibly killed,
an upgrade lock remains until an operator resolves recovery. Stale SNMP walks
fail and release their locks automatically; committed rows remain available. Keep workers running for
queued jobs; the API itself never runs upgrade commands.

## API integration

All `/api/v1` routes require `Authorization: Bearer <token>`. No cookies or CORS
allowlist are used. Authentication is a single shared service identity. Remote
errors omit command output so credentials and configuration do not leak into logs.

| Method | Endpoint | Result |
| --- | --- | --- |
| GET | `/api/v1/devices` | Available inventory IDs and lock groups |
| POST | `/api/v1/plan` | Normalized request and ordered inherited steps; no device commands |
| POST | `/api/v1/snmp/walks` | `202` and persistent SNMP v3 job ID |
| GET | `/api/v1/snmp/walks/{id}` | Walk status and per-OID progress/errors |
| GET | `/api/v1/snmp/walks/{id}/results?after=0&limit=500` | Cursor-paginated result rows, including partial data |
| POST | `/api/v1/jobs` | `202` and persistent queued job |
| GET | `/api/v1/jobs?limit=100&offset=0` | Jobs, newest first |
| GET | `/api/v1/jobs/{id}` | Status, normalized request, result and pre/post reports |
| GET | `/api/v1/jobs/{id}/events?after=0&limit=500` | Ordered event page; next cursor is last `seq` |
| GET | `/api/v1/jobs/{id}/backup` | Authenticated UCS download |
| POST | `/api/v1/jobs/{id}/cancel` | Cancel queued job; running jobs cannot be blindly cancelled |
| POST | `/api/v1/jobs/{id}/rollback` | `202`; queue return to original volume |
| POST | `/api/v1/jobs/{id}/resolve` | Explicit operator recovery acknowledgment and lock release |

Use a unique `Idempotency-Key` for job creation and rollback. Reuse the same key
and identical body if a request times out: the server returns the original job.
Using that key with different parameters returns `409`. Invalid job arguments are
`422`, missing resources `404`, and missing/invalid tokens `401`. Idempotency is
optional for interactive callers but strongly recommended for integrations.

```python
import os
import uuid
import httpx

headers = {"Authorization": "Bearer " + os.environ["F5_API_TOKEN"]}
request = {
    "device": "bigip-a",
    "install": True,
    "reboot": True,
    "target_volume": "HD1.2",
    "allowed_source_versions": ["16.1.5"],
    "images": [
        {
            "name": "BIGIP-approved.iso",
            "sha256": os.environ["APPROVED_IMAGE_SHA256"],
            "version": "17.1.2",
            "build": "0.0.5",
            "kind": "image",
        }
    ],
    "backup_directory": "maintenance/site-a",
    "drain_percent": 90,
    "drain_timeout": 600,
    "reboot_on_drain_timeout": True,
}
# Persist this key in the integrating application before sending.
key = str(uuid.uuid4())
with httpx.Client(base_url="https://upgrade.example.net", headers=headers) as api:
    plan = api.post("/api/v1/plan", json=request)
    plan.raise_for_status()
    response = api.post("/api/v1/jobs", json=request, headers={"Idempotency-Key": key})
    response.raise_for_status()
    job_id = response.json()["id"]
    # Poll until succeeded, failed, cancelled, recovery_required or resolved.
    job = api.get(f"/api/v1/jobs/{job_id}").json()
    # When a rollback is wanted, persist and reuse a separate idempotency key:
    # api.post(f"/api/v1/jobs/{job_id}/rollback", json={},
    #          headers={"Idempotency-Key": rollback_key})
```

Version/build examples are illustrative, not approved upgrade paths. Validate
vendor-supported source/target compatibility, platform support, license/service
check dates and available disk space before scheduling. The allowlist is your
explicit policy, not an embedded F5 compatibility database.

CLI examples (credentials come from `.env`):

```bash
f5-upgrade devices
f5-upgrade plan --device bigip-a --install --images approved-images.json \
  --target-volume HD1.2 --allowed-source-version 16.1.5 --no-reboot
f5-upgrade submit --device bigip-a --install --images approved-images.json \
  --target-volume HD1.2 --allowed-source-version 16.1.5 --reboot \
  --drain-percent 90 --drain-timeout 600 --reboot-on-drain-timeout \
  --backup-directory maintenance/site-a --idempotency-key change-123-bigip-a
f5-upgrade submit --device bigip-a --create-ucs --ucs-unencrypted
f5-upgrade job JOB_UUID
f5-upgrade events JOB_UUID
f5-upgrade rollback JOB_UUID --idempotency-key change-123-bigip-a-rollback
```

## Validation, rollback and recovery

Reports include WAF policy names/counts and per-object plus up/total counts for
virtual servers, pools, nodes and virtual addresses (the `virtual_ips` category).
All partitions are queried recursively. "Up" requires availability `available`
and enabled state `enabled`; unknown/unchecked status does not count as up.
ASM not provisioned is a recorded zero; an ASM command failure is a failed
collection, not zero. Object inventory changes and any previously up object
becoming unhealthy produce a failed validation even if aggregate up counts match.
No-reboot jobs capture a post-operation snapshot of the current boot volume;
they cannot validate the runtime health of software not yet booted.

Rollback is generated automatically from the pre-upgrade record, and is executed
only when requested. It reboots to the saved original volume, verifies its
version/build, restores any tracked address drain state, and validates against the
original baseline. It refuses to act if the current active volume/version/build
no longer match the upgrade job's target, or the original volume has been replaced.
A staged-only job must first actually boot its target before rollback applies.
Rollback does not import a UCS or reconstruct a deleted partition. The original
partition must remain intact; configuration changes made after the upgrade are
not carried back automatically.

States: `queued → running → succeeded | failed | recovery_required`. `cancelled`
is only for queued jobs; `resolved` means an operator has reconciled recovery,
not that the original job succeeded. After a known completed operation, health
regressions produce `failed` and retain the device/group lock (`lock_held: true`),
blocking follow-on upgrades. A rollback job may take over that known-failed job’s
lock immediately. Alternatively, use the recovery resolution endpoint after
inspection. If that rollback itself fails or is interrupted, it retains the lock. An exception after a
mutation or a worker with no heartbeat for 120 seconds produces `recovery_required`
and retains its device/group lock. No automatic retry is performed across a crash.

To recover, stop the recorded worker process/container, inspect the device and
its software installation/boot state, and restore any address changes recorded in
`result.drain.address_states` that remain on the current volume. Then:

```bash
f5-upgrade resolve JOB_UUID --worker-stopped --device-inspected \
  --note 'Worker stopped; boot and address states inspected and reconciled'
# If currently running that job's upgrade target, request rollback as above.
```

The same API request body is
`{"worker_stopped":true,"device_inspected":true,"note":"..."}`. These are operator
attestations: the API cannot prove a remote worker has been stopped. Do not release
a lock while an old SSH command, install, or reboot is still in flight. Resolving
a job never issues device commands or marks the original work successful.

Backup the database with SQLite's online backup API or stop all API/workers before
copying the database and WAL files together. Keep backups and jobs as one retention
set. No automatic retention purge removes rollback records or UCS archives.

## Scope and validation

This release targets individual BIG-IP appliances and VEs with bash, SFTP and
tmsh. Slot-qualified chassis output is rejected; F5OS, multi-blade VIPRION,
vCMP host orchestration, HA failover sequencing and cross-host workers are not
implemented. HA members can be serialized with `lock_group`, but peer readiness
and traffic failover remain the caller's responsibility.

Automated tests use synthetic tmsh fixtures, mocked SSH/device behavior and actual
SQLite concurrency. They cover requests, authentication, checksum checks, drain
restoration, post-validation regression, verified reboot, rollback and crash
fencing. **A real-device upgrade/rollback acceptance test is still required for
your exact TMOS releases before production use.** Unrecognized output fails the
job rather than silently reporting healthy counts.

```bash
pip install -e '.[test]'
pytest -q
ruff check .
ruff format --check .
node --check f5_upgrade/static/app.js
```

Command references:
[F5 virtual-address](https://clouddocs.f5.com/cli/tmsh-reference/latest/modules/ltm/ltm_virtual-address.html),
[F5 UCS](https://clouddocs.f5.com/cli/tmsh-reference/latest/modules/sys/sys_ucs.html),
[F5 image installation](https://clouddocs.f5.com/cli/tmsh-reference/latest/modules/sys/sys_software_image.html),
[F5 boot volumes](https://clouddocs.f5.com/cli/tmsh-reference/latest/modules/sys/sys_software_volume.html).

## Asynchronous SNMP v3 walks

Submit an IPv4/IPv6 address and an array of numeric OID subtrees. No inventory
entry or F5-specific MIB is needed. Walks are SNMP v3 only, using GETNEXT over UDP
from the worker to the target (default port 161). SSH is used for upgrade operations.
Each root is walked separately, in request order, and stops at its subtree boundary.
These are subtree walks, not GET requests: to retrieve a scalar instance such as
`sysDescr.0`, walk its parent `1.3.6.1.2.1.1.1`.

Copy `snmp-profiles.example.json` into `config/snmp-profiles.json` and set
`F5_SNMP_PROFILES_FILE=/config/snmp-profiles.json` in Docker's `.env`.
For native installations, use `/etc/f5-upgrade/snmp-profiles.json`. Set
`F5_SNMP_AUTH_PASSWORD` and `F5_SNMP_PRIV_PASSWORD` in the same environment file.
API and workers need the same profiles; workers need the referenced secrets.
Restart API/workers after changing configuration. Leave this optional variable
unset to keep SNMP submission disabled on an existing installation. An SNMP-only
installation can use an empty `[]` device inventory.

Profiles contain environment variable **names**, never literal passwords.
`authPriv` with SHA256 and AES128 is the default. Authentication protocols are
`SHA` (SHA-1), `SHA224`, `SHA256`, `SHA384`, and `SHA512`; privacy is `AES128`.
For `authNoPriv`, omit `priv_password_env`. For `noAuthNoPriv`, omit both password
references. Passphrases must contain at least eight UTF-8 bytes. Profiles can set
`context_name`. API requests select a profile by ID, defaulting to `default`;
credentials are never accepted in a walk request or saved in job records.

```http
POST /api/v1/snmp/walks
Authorization: Bearer <token>
Content-Type: application/json
Idempotency-Key: walk-request-001

{
  "device_ip": "192.0.2.10",
  "oids": ["1.3.6.1.2.1.1", "1.3.6.1.4.1.3375"],
  "profile": "default"
}
```

The `202` response uses the normal job envelope, including `id`, `kind: snmp_walk`,
`status: queued`, and normalized `request`. Persist the ID and poll:

- `GET /api/v1/snmp/walks/{id}` for status and `result.oids`, including each root's status, row count and error.
- `GET /api/v1/snmp/walks/{id}/results?after=0&limit=500` for rows.

```json
{
  "id": "<job-id>",
  "status": "succeeded",
  "finished": true,
  "rows": [
    {
      "seq": 42,
      "root_oid": "1.3.6.1.2.1.1",
      "oid": "1.3.6.1.2.1.1.1.0",
      "type": "OctetString",
      "value": "QklHLUlQ",
      "encoding": "base64"
    }
  ],
  "next_cursor": 42,
  "has_more": false
}
```

Pass `next_cursor` as `after` on the next request. Cursors are opaque, monotonically
increasing integers, not row counts. Continue polling the same cursor after an
empty page. Stop only when `finished` is true **and** `has_more` is false. While a
job runs, `has_more: false` only means no additional rows are currently committed.
Use a short delay (for example, one second) between empty-page polls.

Values are strings to preserve large Counter64 integers exactly. Octet strings
(including ordinary textual SNMP strings) use base64 to preserve raw bytes;
other values use `encoding: text` with their ASN.1 type. IP addresses are dotted
text. Overlapping roots can return duplicate OIDs, distinguished by `root_oid`.
An empty accessible subtree succeeds with zero rows; SNMP cannot distinguish an
absent subtree from one hidden by the agent's access controls.

| Request option | Default | Bounds / meaning |
| --- | --- | --- |
| `oids` | Required | 1–64 unique numeric roots; optional leading dot |
| `port` | 161 | 1–65535 |
| `timeout` | 2 | 0.1–30 seconds per SNMP request |
| `retries` | 1 | 0–5 retries per request |
| `job_timeout` | 300 | 1–3600 seconds for the whole running job, excluding queue time |
| `max_rows` | 10000 | 1–100000 rows across all roots |

Results also have a fixed 16 MiB serialized-data limit per job. Hitting a row/byte
limit sets `result.truncated: true` and fails the job; hitting its time limit also
fails it. Committed partial rows remain downloadable. Individual SNMP engine/PDU
errors are reported per root, and subsequent roots are attempted while time and
space remain. `succeeded` means every requested root finished. Failed, cancelled,
and stale-worker jobs are terminal; they are never silently replayed.

The existing bearer token, idempotency keys, generic job listing, job events, and
queued-job cancellation apply. Walks have no rollback action. Workers serialize
walks to the same canonical IP/port while allowing different targets concurrently.
SNMP locks are independent of upgrade locks, so reads may overlap an upgrade and
observe transitional state. Add workers using the existing Docker/systemd setup;
no broker or additional database is needed. Job/result retention is currently
manual; keep the SQLite volume sized for retained walk history.
