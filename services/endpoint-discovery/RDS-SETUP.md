# Discovery database on the existing RDS instance

Create a new logical PostgreSQL database named `endpoint_discovery` and a
dedicated login of the same name on the RDS PostgreSQL instance already hosting
NetBox. The API owns its observation/history tables there. Remote pollers have
no PostgreSQL credentials; the portal uses the discovery HTTP API.

This procedure provisions database objects only. It needs no new RDS instance,
extensions, instance parameter changes, certificate rotation, reboot, or NetBox
schema changes. Choose different unique names if these are already in use.

## Prerequisites

- Confirm the existing instance is **RDS for PostgreSQL** and record its engine
  major version, actual RDS DNS endpoint, port and AWS region/partition. The
  service's PostgreSQL integration tests currently use PostgreSQL 17; validate
  the actual RDS engine version in staging before rollout.
- Use an authorized database administrator for provisioning. RDS's
  `rds_superuser` administrator can create roles/databases but is not a normal
  PostgreSQL superuser. Never grant `rds_superuser` to the application login.
  See [AWS's administrator role reference](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Roles.rds_superuser.html).
- Run `psql` from a controller/bastion with private access to RDS. Permit the API
  host's security group to reach RDS TCP 5432; pollers do not need this rule.
- Obtain the correct CA bundle from
  [AWS's regional certificate download table](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/UsingWithRDS.SSL.html).
  Save it as a controlled deployment input. Use the bundle for your region and
  partition; commercial, GovCloud and China download endpoints can differ.
- Prepare a strong unique database password in the secret manager. Record its
  secret reference, not its value, in deployment inventory.

## 1. Connect as the provisioning administrator with verified TLS

Replace the non-secret example values below. `psql -W` prompts for the admin
password without putting it in the command line. A managed secret integration
may instead supply an ephemeral, mode-0600 `PGPASSFILE`; remove it after use.
Do not echo passwords or full connection URLs into logs.

```bash
export PGHOST='EXISTING_RDS_ENDPOINT'
export PGPORT='5432'
export PGSSLMODE='verify-full'
export PGSSLROOTCERT='/secure/pki/rds-ca-bundle.pem'
psql -X -W -v ON_ERROR_STOP=1 -U RDS_ADMIN_LOGIN -d postgres
```

Use the certificate's RDS DNS hostname, rather than an IP or arbitrary CNAME.
`verify-full` requires both trusted signing certificates and a matching hostname.
These settings apply to the current client, so no shared `rds.force_ssl`
parameter change is needed. See [AWS's PostgreSQL TLS connection examples](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/ssl-certificate-rotation-postgresql.html).

At the `psql` prompt, inspect names and privileges first:

```sql
SELECT version();
SELECT datname, pg_get_userbyid(datdba) AS owner
FROM pg_database WHERE datname = 'endpoint_discovery';
SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
       rolreplication, rolbypassrls
FROM pg_roles WHERE rolname IN (current_user, 'endpoint_discovery');
```

For first provisioning, the new database and role must be absent. If either
already exists, stop and verify ownership/purpose before reconciling it. Roles
are shared across all databases on the instance; never overwrite an unrelated
login with the same name.

## 2. Create the dedicated login and database

Run the following in that administrator `psql` session. This is a **first-create
procedure**, not a script to blindly rerun. Do not wrap `CREATE DATABASE` in
`BEGIN`/`COMMIT`; PostgreSQL requires it outside a transaction.

```sql
CREATE ROLE endpoint_discovery
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Session-only setting; does not alter the shared instance parameter group.
SET password_encryption = 'scram-sha-256';
\password endpoint_discovery

-- Allow the administrator to assign ownership to the newly created role.
SELECT current_user AS provisioning_login \gset
GRANT endpoint_discovery TO :"provisioning_login";

CREATE DATABASE endpoint_discovery
  OWNER endpoint_discovery TEMPLATE template0 ENCODING 'UTF8';

REVOKE ALL ON DATABASE endpoint_discovery FROM PUBLIC;
ALTER ROLE endpoint_discovery IN DATABASE endpoint_discovery
  SET search_path = public;

\connect endpoint_discovery
ALTER SCHEMA public OWNER TO endpoint_discovery;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO endpoint_discovery;

-- Remove the temporary ownership-assignment grant from the administrator.
REVOKE endpoint_discovery FROM :"provisioning_login";
```

At `\password`, enter the prepared application password twice. This avoids a
plaintext password in SQL history. The temporary membership is granted to the
administrator, never the other way around. Creating a database for another owner
requires the ability to assume that role; see
[PostgreSQL CREATE DATABASE](https://www.postgresql.org/docs/17/sql-createdatabase.html).
If provisioning stops partway through, inspect and reconcile the objects and
temporary membership before continuing; do not drop the database to restart.
On newer PostgreSQL releases, the role creator can retain an automatically
granted `ADMIN` membership with `INHERIT` and `SET` disabled after this revoke;
that is distinct from the temporary ownership-assignment grant. Preserve the
administrator's established role-management privileges.

The application owns only its new database/schema. Explicit public-schema
permissions make the intended access clear across differing PostgreSQL/template
defaults; see [PostgreSQL schema permissions](https://www.postgresql.org/docs/17/ddl-schemas.html).
No extension or administrator privilege is required by `manage migrate`.

Database ownership lets this login migrate and maintain its own tables. A
separate DDL owner/runtime login design would require additional grants and
separate migration/runtime configuration; that split is not supplied by the
current role. The device read-only requirement does not prohibit storing and
updating discovery data.

## 3. Verify as the application login

Exit the administrator session, then reconnect with the same TLS environment:

```bash
psql -X -W -v ON_ERROR_STOP=1 -U endpoint_discovery -d endpoint_discovery
```

```sql
SELECT current_database(), current_user;
SHOW search_path;
SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid();
SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
FROM pg_roles WHERE rolname = current_user;
SELECT r.rolname AS membership
FROM pg_roles r WHERE pg_has_role(current_user, r.oid, 'MEMBER')
  AND r.rolname <> current_user;

-- DDL/DML permission smoke test; all objects are rolled back.
BEGIN;
CREATE TABLE public.discovery_permission_check (id integer PRIMARY KEY);
INSERT INTO public.discovery_permission_check VALUES (1);
SELECT * FROM public.discovery_permission_check;
UPDATE public.discovery_permission_check SET id = 2;
DELETE FROM public.discovery_permission_check;
ROLLBACK;
```

Expect database/user `endpoint_discovery`, search path `public`, TLS `ssl = t`,
all five elevated role flags false, and no unexpected role memberships.
`pg_database_owner` is an expected implicit membership while connected as the
owner of this database on PostgreSQL versions that provide it. Successful
`verify-full` connection checks certificate identity; `pg_stat_ssl` alone only
confirms encryption. The permission check must leave no table behind.

Audit isolation with the DBA: this new login must not inherit NetBox's login or
read/write roles, and must have no privileges on NetBox application tables.
PostgreSQL databases may allow `PUBLIC CONNECT` by default, so a new dedicated
login alone does not prove it cannot connect to other databases. Check the
existing database's grants and table privileges under your established access
policy. This runbook revokes `PUBLIC` only on the **new** database/schema;
changing existing NetBox grants requires a separate reviewed change.

## 4. Configure the server and create application tables

Set the server's protected `discovery_database_url` secret to:

```text
postgresql+psycopg://endpoint_discovery:ENCODED_PASSWORD@EXISTING_RDS_ENDPOINT:5432/endpoint_discovery?sslmode=verify-full&sslrootcert=/config/database-ca.pem
```

The driver prefix must be `postgresql+psycopg://`. Percent-encode the password
component, including `@`, `/`, `:`, `%`, `#`, and `?` if present; do not encode
the entire URL. Construct this inside the protected secret workflow, not in a
shell command that prints it. The shipped service uses a persistent password;
it does not implement automatic IAM auth-token renewal.

In server inventory, set:

```yaml
discovery_database_url: "{{ vault_discovery_database_url }}"
discovery_database_ca_file: /secure/pki/rds-ca-bundle.pem
```

The role installs that bundle under `config/database-ca.pem`. The read-only
config directory is mounted at `/config` in both the API and management
containers. This is independent of the CA used by remote pollers to trust the
API's HTTPS endpoint. For manual installation, create the same file with
root:10001 ownership and mode `0640` inside a root:10001 mode-0750 config
directory, and put the URL in the protected server `.env` (mode `0600`).

The Ansible role builds the image, installs access/target configuration, runs
migrations, imports assignments and starts the service. Equivalent commands
for an already configured server, from its service directory, are:

```bash
docker compose -f compose.yaml build
docker compose -f compose.yaml run --rm --no-deps manage migrate
docker compose -f compose.yaml run --rm --no-deps manage import-targets /config/targets.json --replace
docker compose -f compose.yaml up -d api
curl --fail --silent --show-error http://127.0.0.1:8092/healthz
```

`--replace` makes the target file authoritative: validate its contents first.
If scheduled NetBox sync owns assignments, use that import path instead, as
described in [the deployment handoff](DEPLOYMENT-HANDOFF.md#assignment-ownership).
Do not start pollers until schema readiness passes. Do not run `docker compose
config` in logs with production environment files: it can expand secrets.

Schema version 1 creates `schema_version`, `devices`, `observations`,
`collections`, `receipts`, and `poller_heartbeats` plus indexes in this database. Verify
using the application `psql` connection:

```sql
SELECT * FROM public.schema_version;
SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public'
ORDER BY tablename;
```

Expect schema version 1, all discovery tables owned by `endpoint_discovery`,
and API `/healthz` HTTP 200. Test the external HTTPS path, a pilot poller report,
and a portal reader query before the remaining rollout.

## Automation requirements

Translate the provisioning sequence into a separate controller/bastion play
with the organization's pinned PostgreSQL collection/driver dependencies. Use
catalog checks/idempotent modules, database creation outside a transaction,
verified TLS, and protected secret lookups (`no_log: true`, `diff: false`).
Reconcile only objects explicitly identified as belonging to this service.
Preserve pre-existing administrator memberships; the temporary grant/revoke
above assumes a newly created application role. Never persist administrator
credentials to API or poller hosts. Application migrations can then run on each
server deployment; database creation and password rotation are distinct steps.

Use SCRAM for newly provisioned credentials and explicitly verify compatibility
with the existing RDS authentication policy. Do not change its instance-wide
authentication settings just for this service. Avoid unbounded retries for
authentication, certificate or ownership errors; fail with a sanitized message.

## Shared capacity, backup and recovery

The new database shares instance CPU, RAM, storage/I/O, connections, maintenance
and failure domains with NetBox. Establish headroom and alert thresholds before
adding all 1,500 devices. Monitor RDS and existing NetBox latency during a pilot
and each rollout batch. Plan retention from measured rows/churn and index sizes,
not device count alone. Daily `manage prune` removes old history but PostgreSQL
vacuum/reuse does not necessarily shrink allocated RDS storage.

The API currently uses [SQLAlchemy's default pool](https://docs.sqlalchemy.org/en/20/core/pooling.html):
up to five retained connections and ten overflow connections per process,
opened on demand. Each management process has a
separate engine/pool. Account for concurrent pruning/import/migration jobs and
future API worker/replica changes within the shared connection budget. Only
the API/management processes connect to RDS; 1,500 devices do not imply 1,500
database sessions. Apply a per-role connection limit only after budgeting those
processes and operational headroom.

Confirm existing RDS automated backups cover the instance and meet retention
requirements. RDS snapshot/PITR recovery is an instance-level operation; follow
[AWS's restore procedure](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.html)
to restore to a separate instance, then recover the discovery database using a
DBA-reviewed database-scoped dump/restore or cutover. Do not restore the shared
production instance over NetBox to roll back discovery. If independent logical
backups are required, schedule protected `pg_dump` backups of this database,
include role/grant provisioning in the recovery plan, and test a restore.

Keep RDS CA trust bundles maintained through the deployment process. Rotate the
application password in the secret manager and RDS together, update the server
environment, recreate API containers and verify readiness. Do not distribute
database secrets to pollers during troubleshooting. A failed certificate check
should be resolved by correcting the endpoint/CA, not weakening `sslmode`.
