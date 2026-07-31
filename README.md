# NetBox @ jtklabs

Env-driven NetBox deployment (dev local / prod on EC2 behind nova.jtklabs.dev/netbox).
See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the gated plan and [VERSIONS.md](VERSIONS.md) for pins.

## Dev quickstart

```bash
./scripts/init-dev-env.sh   # generates .env + env/*.env with fresh local secrets
docker compose build
docker compose up -d        # first boot runs migrations (~several minutes)
```

NetBox: http://127.0.0.1:8080 — local superuser `admin`, password in your generated `.env` (`SUPERUSER_PASSWORD`). No secrets are committed to this repo; `env/*.env.example` are the templates.

NetBox UI: dev also runs the discovery stack (Diode + orb-agent — see [discovery/README.md](discovery/README.md)).

## Layout

- `docker-compose.yml`, `configuration/`, `env/*.example` — imported from netbox-docker at the tag in [VERSIONS.md](VERSIONS.md) (deviation: the postgres service lives in `compose/dev.yml`; prod uses RDS)
- `compose/` — env overlays: `dev.yml`, `prod.yml`, `discovery.yml`, `proxy.yml` (dev rehearsal of the prod subpath+SSO topology)
- `plugins/netbox-quotes/` — our quotes/serial-matching plugin; `Dockerfile-Plugins` builds the image with it + PyPI plugins
- `apache/netbox.conf` — prod vhost include (mellon SSO, header injection, static mapping)
- `deploy/` + `docs/RUNBOOK-*.md` — 30-day AMI redeploy automation and procedures
- `scripts/clean_inventory.py` — standalone utility (unrelated to the deployment): cleans an inventory CSV by stripping component serials (modules, PSUs, line cards, optics) via the Cisco Product Information API, keeping real devices. `--mode switches` narrows it to switches only. Non-Cisco and unknown serials are always kept.

## Prod (summary)

One-time: prepare `/data/netbox-secrets` on the data disk (see the header of
[deploy/bootstrap.sh](deploy/bootstrap.sh)). Every redeploy after that is
automatic via user-data/systemd. Procedures: [docs/RUNBOOK-redeploy.md](docs/RUNBOOK-redeploy.md).
