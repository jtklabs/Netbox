# Version pins

| Component | Pin | Since | Notes |
|---|---|---|---|
| NetBox | v4.6.7 | 2026-08-04 | Bumped 4.6.5 → 4.6.7 (skipping 4.6.6, superseded). Both are patch releases: bug fixes and query-count reductions, no schema or REST/GraphQL breaks. In-place upgrade verified in dev against an existing database. |
| netbox-docker image | `netboxcommunity/netbox:v4.6.7-5.0.2` | 2026-08-04 | Base of `Dockerfile-Plugins`; local image tag `netbox-custom:v4.6.7-5.0.2`. |
| netbox-docker support files | tag `5.0.2` (commit `5adc62fe3fa65163c4ef63733bdcbd3e59b5c544`) | 2026-07-28 (unchanged by the 4.6.7 bump — same support-files version) | Imported verbatim: `docker-compose.yml`, `configuration/` (plugins.py edited), `env/` (secrets regenerated). To upgrade: diff against the new upstream tag. |
| netbox-quotes (ours) | 0.1.0 | 2026-07-29 | Local plugin in `plugins/netbox-quotes`, installed by Dockerfile-Plugins. Replaces netbox-contract (dropped 2026-07-29 — see PROJECT_PLAN.md D9). |
| netbox-refresh (ours) | 0.1.0 | 2026-07-31 | Local plugin in `plugins/netbox-refresh`: EoL dates, replacement models, cost, Cisco EoX sync, refresh report. Replaces netbox-lifecycle (D10). |
| ~~netbox-lifecycle~~ | removed 2026-07-31 | — | Third-party EoL plugin, superseded by netbox-refresh. Tables dropped with `migrate netbox_lifecycle zero` before removal. |
| Postgres (dev) | **16-alpine** | 2026-07-29 | Aligned to prod RDS (PostgreSQL 16.13). |
| Valkey | 9.1-alpine | 2026-07-28 | Queue (DB 0, AOF) + cache (DB 1). |

| ~~netboxlabs-diode-netbox-plugin~~ | removed 2026-08-09 | — | Dropped with the Diode stack (D12). Its one table, `netbox_diode_plugin_setting`, is dropped with `migrate netbox_diode_plugin zero` **while the plugin is still installed** — once the image no longer ships it, the app is unknown and the table can only be dropped by hand. |

*Diode + orb-agent were removed 2026-08-09 (see PROJECT_PLAN.md D12). Discovery is being rebuilt in-house; the pins above are gone from the tree but remain in git history if they are ever needed again.*
