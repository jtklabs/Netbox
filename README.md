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

Layout: `docker-compose.yml`, `configuration/`, `env/` are imported from netbox-docker at the tag recorded in VERSIONS.md; our changes live in `compose/` overlays, `Dockerfile-Plugins`, `plugin_requirements.txt`, and `configuration/plugins.py`.
