# NetBox @ jtklabs

Env-driven NetBox deployment (dev local / prod on EC2 behind nova.jtklabs.dev/netbox).
See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the gated plan and [VERSIONS.md](VERSIONS.md) for pins.

## Dev quickstart

From a fresh clone, one command does everything — env files, image build, stack, and waiting for NetBox:

```bash
./scripts/dev-up.sh
```

It is idempotent, so it is also how to restart. It prints the URL and the generated
admin password when NetBox is ready (first boot runs all migrations, ~10 minutes).

Doing it by hand is the same three steps:

```bash
./scripts/init-dev-env.sh   # generates .env + env/*.env with fresh local secrets
docker compose build
docker compose up -d
```

**`scripts/init-dev-env.sh` is not optional.** A fresh clone ships only
`env/*.example` templates, and `.env` is what selects the compose overlay chain —
without it compose runs the base file alone, which has no database. Re-running the
script is safe: it creates only what is missing and keeps the shared passwords in
`env/netbox.env`, `postgres.env`, `redis.env` and `redis-cache.env` consistent.

NetBox: http://127.0.0.1:8080 — local superuser `admin`, password in your generated `.env` (`SUPERUSER_PASSWORD`). No secrets are committed to this repo.

### Reaching dev from another machine

By default everything binds to loopback, so a dev host is not exposed. On a
shared dev server, put NetBox behind the bundled HTTPS proxy — it serves at
**`/netbox`, the same path prod uses**, so the subpath and static-file mapping
are exercised every day rather than only in a drill:

```bash
./scripts/dev-tls-cert.sh netbox-dev.example.com    # or an IP
```

That prints the `.env` lines to set (`BIND_ADDRESS`, `DEV_HOSTNAME`,
`CSRF_TRUSTED_ORIGINS`, and the `compose/dev-proxy.yml` overlay). Then
`docker compose up -d` and browse to `https://netbox-dev.example.com:8443/netbox/`.
The certificate is self-signed, so browsers warn — the point is keeping
credentials off plaintext HTTP, not proving identity.

Two things to know:

- **`CSRF_TRUSTED_ORIGINS` is required** as soon as NetBox is reached by
  anything other than localhost. Without it the login POST fails as a CSRF
  error even though the page loads.
- Setting `DEV_PROXY_SSO_USER` additionally simulates the prod Mellon header
  handoff, including group sync — useful for testing SSO behaviour. Leave it
  empty for normal local logins.

Exposing the stack also exposes the Diode ingest port, which is plaintext gRPC.
Prefer binding to a specific LAN address over `0.0.0.0`, and see
[collector/README.md](collector/README.md) before exposing Diode beyond a
trusted network.

## Prod image

```bash
./scripts/prod-build.sh
```

Builds the production image and verifies every plugin loads inside it, so a broken
plugin fails at bake time rather than during a redeploy. This is the step to add to
the monthly AMI bake — see [docs/FIRST-BOOT.md](docs/FIRST-BOOT.md).

NetBox UI: dev also runs the discovery stack (Diode + orb-agent — see [discovery/README.md](discovery/README.md)).

## Layout

- `docker-compose.yml`, `configuration/`, `env/*.example` — imported from netbox-docker at the tag in [VERSIONS.md](VERSIONS.md) (deviation: the postgres service lives in `compose/dev.yml`; prod uses RDS)
- `compose/` — env overlays: `dev.yml`, `prod.yml`, `discovery.yml`, `proxy.yml` (dev rehearsal of the prod subpath+SSO topology)
- `plugins/netbox-quotes/` — our quotes/serial-matching plugin; `Dockerfile-Plugins` builds the image with it + PyPI plugins
- `plugins/netbox-refresh/` — our hardware-lifecycle plugin: EoL dates on device/module types, replacement model links, replacement cost, Cisco EoX sync (`manage.py sync_cisco_eol`) and the refresh cost report at **Hardware Refresh › Refresh Report**
- `apache/netbox.conf` — prod vhost include (mellon SSO, header injection, static mapping)
- `collector/` — remote collector kit: drop it on a box at a remote site and it discovers locally, pushing outbound to the central Diode. Includes a custom-Python worker skeleton. See [collector/README.md](collector/README.md); mint credentials with `scripts/new-collector.sh`
- `deploy/` + `docs/RUNBOOK-*.md` — 30-day AMI redeploy automation and procedures
- `scripts/clean_inventory.py` — standalone utility (unrelated to the deployment): cleans an inventory CSV by stripping component serials (modules, PSUs, line cards, optics) via the Cisco Product Information API, keeping real devices. Only rows whose name contains parentheses — the `(1)`/`(2)` duplicates — are sent to Cisco; `--check-all` overrides. On those rows a "no record at Cisco" also removes the row (`--keep-unknown` disables). Rows with plain names are never looked up or removed, and a failed lookup never removes anything. `--mode switches` narrows the result to switches only.

## Prod (summary)

One-time: prepare `/data/netbox-secrets` on the data disk (see the header of
[deploy/bootstrap.sh](deploy/bootstrap.sh)). Every redeploy after that is
automatic via user-data/systemd. Procedures: [docs/RUNBOOK-redeploy.md](docs/RUNBOOK-redeploy.md).
