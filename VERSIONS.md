# Version pins

| Component | Pin | Since | Notes |
|---|---|---|---|
| NetBox | v4.6.5 | 2026-07-28 | **Adopted** — spike passed 2026-07-28 (both plugins fully functional). Bump to v4.6.6 when its Docker image publishes (~24h build cycle). |
| netbox-docker image | `netboxcommunity/netbox:v4.6.5-5.0.2` | 2026-07-28 | Base of `Dockerfile-Plugins`; local image tag `netbox-jtk:v4.6.5-5.0.2`. |
| netbox-docker support files | tag `5.0.2` (commit `5adc62fe3fa65163c4ef63733bdcbd3e59b5c544`) | 2026-07-28 | Imported verbatim: `docker-compose.yml`, `configuration/` (plugins.py edited), `env/` (secrets regenerated). To upgrade: diff against the new upstream tag. |
| netbox-quotes (ours) | 0.1.0 | 2026-07-29 | Local plugin in `plugins/netbox-quotes`, installed by Dockerfile-Plugins. Replaces netbox-contract (dropped 2026-07-29 — see PROJECT_PLAN.md D9). |
| netbox-lifecycle | 1.1.9 | 2026-07-28 | 4.6 blocker fixed upstream in 1.1.9. |
| Postgres (dev) | 18-alpine (upstream default) | 2026-07-28 | Will be aligned to the RDS engine major version once confirmed (Gate 0 Q1). |
| Valkey | 9.1-alpine | 2026-07-28 | Queue (DB 0, AOF) + cache (DB 1). |
| Diode services | 2.1.0 (`DIODE_TAG`) | 2026-07-29 | ingester/reconciler/auth. Compose adapted from netboxlabs/diode `release` branch (`diode-server/docker/`); `nginx.conf` + `bootstrap-clients.sh` vendored verbatim into `discovery/`. |
| netboxlabs-diode-netbox-plugin | 1.14.1 | 2026-07-29 | Plugin `netbox_diode_plugin`; supports NetBox 4.4.10–4.6.99. |
| orb-agent | 2.11.0 | 2026-07-29 | Backends in use: device_discovery (NAPALM), snmp_discovery. |
| Diode support images | hydra v26.2.0, nginx 1.27-alpine, postgres 16-alpine, redis-stack latest | 2026-07-29 | Per upstream compose (nginx pinned by us; redis-stack unpinned upstream). |
