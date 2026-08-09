# NetBox deployment

Env-driven NetBox deployment (dev local / prod on EC2 behind netbox.example.com/netbox).
See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the gated plan and [VERSIONS.md](VERSIONS.md) for pins.

## Dev quickstart

One command does everything — env files, image build, stack, and waiting for NetBox:

```bash
bash scripts/dev-up.sh
```

(Invoked with `bash` so it works from a ZIP download too, where the executable
bit is lost. From a git clone `./scripts/dev-up.sh` is equivalent.)

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

### Deployed from a ZIP rather than a clone?

A ZIP download drops the executable bit on every script, so `./scripts/dev-up.sh`
fails with "permission denied". Start with the entry point invoked through bash —
it restores the modes for everything else:

```bash
bash scripts/init-dev-env.sh
```

**Before running it on a host that already has a stack, keep your existing
`.env` and `env/*.env`.** They are gitignored, so a ZIP does not contain them,
and if they are missing the script generates *new* secrets — including a new
database password, which will not match the existing PostgreSQL volume, and a
new `SECRET_KEY`, which invalidates every session. Copy the old files back
before starting anything. Extracting a ZIP over the existing directory keeps
them; extracting into a fresh directory does not.

A ZIP also has no `.git`, so there is no way to pull later updates — prefer
`git clone` where your policies allow it.

NetBox: http://127.0.0.1:8080 — local superuser `admin`, password in your generated `.env` (`SUPERUSER_PASSWORD`). No secrets are committed to this repo.

### Reaching dev from another machine

Pass the hostname or IP people will use:

```bash
./scripts/dev-up.sh netbox-dev.example.com
```

That is the whole procedure — no variables to edit and no certificate step. It
generates the TLS certificate, writes every setting it needs into `.env`,
enables the reverse proxy and starts the stack, then prints the URL. Re-run it
any time to restart, or with a different name to move it. `./scripts/dev-up.sh
--local` goes back to loopback-only.

NetBox is served at **`/netbox`, the same path production uses**, so the subpath
and the static-file mapping are exercised every day rather than only in a drill.
The certificate is self-signed, so browsers warn once — the point is keeping
credentials off plaintext HTTP, not proving identity.

Two things worth knowing:

- Setting `DEV_PROXY_SSO_USER` in `.env` additionally simulates the production
  Mellon header handoff, including group sync, so SSO behaviour can be tested
  on the same path. Leave it empty for normal local logins.
- The stack binds to all interfaces so one command works on both a Linux server
  and Docker Desktop. On Linux you can narrow `BIND_ADDRESS` in `.env` to a
  single interface address afterwards and it is respected.

## Prod image

```bash
./scripts/prod-build.sh
```

Builds the production image and verifies every plugin loads inside it, so a broken
plugin fails at bake time rather than during a redeploy. This is the step to add to
the monthly AMI bake — see [docs/FIRST-BOOT.md](docs/FIRST-BOOT.md).


## Layout

- `docker-compose.yml`, `configuration/`, `env/*.example` — imported from netbox-docker at the tag in [VERSIONS.md](VERSIONS.md) (deviation: the postgres service lives in `compose/dev.yml`; prod uses RDS)
- `compose/` — env overlays: `dev.yml`, `prod.yml`, `dev-proxy.yml` (HTTPS proxy serving dev at `/netbox`, the prod path)
- `plugins/netbox-quotes/` — our quotes/serial-matching plugin; `Dockerfile-Plugins` builds the image with it + PyPI plugins
- `plugins/netbox-refresh/` — our lifecycle plugin, shown in the UI as **Lifecycle** (the package keeps its original `netbox_refresh` name and `/api/plugins/refresh/` paths — renaming would cost migrations and API churn for nothing). Two halves:
  - **Hardware** — EoL dates on device/module types, replacement model links, replacement cost, Cisco EoX sync (`manage.py sync_cisco_eol`), and the refresh cost report at **Lifecycle › Refresh Report**
  - **Software** — a version catalogue (release date, image link/checksum/size), effective-dated **standards** listing the versions explicitly approved for a device type or platform, per-device running versions with provenance and freshness, per-device "do not upgrade" exemptions, and the **Compliance Report** / **Version Rollup**. Collectors push readings to `POST /api/plugins/refresh/device-software/report/`. NetBox stores the *link* to a code image, never the image — set `IMAGE_BASE_URL` to your internal image server and a version needs only a filename to get a download link on the version, device and device-type pages
- `apache/netbox.conf` — include for the **existing** Apache/Mellon server (a separate host): protects `/netbox`, strips spoofed identity headers, proxies over the private network + static mapping
- `deploy/` + `docs/RUNBOOK-*.md` — 30-day AMI redeploy automation and procedures (incl. `RUNBOOK-noexec-recovery.md` for hardened hosts where containers die at create-task with "permission denied")
- `scripts/prepare-docker-host.sh` — turn a fresh Ubuntu 24 / RHEL 9 box into a Docker host for these stacks: engine + Compose v2, **all Docker networking pinned to CGNAT** (100.64.0.0/10 — the 172.17/12 defaults collide with our networks)
- `testing/sso-idp/` — throwaway SAML IdP + real mod_auth_mellon SP for rehearsing the prod SSO config against a real round trip; see its README
- `scripts/clean_inventory.py` — standalone utility (unrelated to the deployment): cleans an inventory CSV by stripping component serials (modules, PSUs, line cards, optics) via the Cisco Product Information API, keeping real devices. Only rows whose name contains parentheses — the `(1)`/`(2)` duplicates — are sent to Cisco; `--check-all` overrides. On those rows a "no record at Cisco" also removes the row (`--keep-unknown` disables). Rows with plain names are never looked up or removed, and a failed lookup never removes anything. `--mode switches` narrows the result to switches only.

## Prod (summary)

Apache + Mellon run on a **separate, already-deployed server** which proxies to
this instance over the private network; the NetBox host runs neither.

One-time setup is [docs/FIRST-BOOT.md](docs/FIRST-BOOT.md): prepare
`/mnt/data_disk/netbox-secrets` on the data disk, set `BIND_ADDRESS=0.0.0.0`,
restrict port 8080 to the Apache server's security group, and add
[apache/netbox.conf](apache/netbox.conf) to that server's vhost. Every redeploy
after that is automatic via user-data/systemd —
[docs/RUNBOOK-redeploy.md](docs/RUNBOOK-redeploy.md).
