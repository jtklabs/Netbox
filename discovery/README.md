# Network discovery (Diode + orb-agent)

Self-hosted "NetBox Discovery community path": the orb-agent scans the network
(NAPALM over SSH + SNMP) and pushes entities into the Diode ingestion stack,
whose reconciler applies them to NetBox through the Diode plugin. No NetBox Labs
license involved. Runs via `compose/discovery.yml` (in the default dev
`COMPOSE_FILE` chain; drop it from `.env` for a core-only stack).

## Pieces

- `agent.yaml` — orb-agent config + scan policies. **No secrets**: `${VARS}` resolve
  from container env, which `compose/discovery.yml` maps from `.env`.
- `nginx/nginx.conf`, `oauth2/bootstrap-clients.sh` — vendored verbatim from
  netboxlabs/diode (release branch); see VERSIONS.md for the pin.
- `oauth2/client/client-credentials.json` — OAuth2 clients (generated, gitignored).
  `scripts/init-dev-env.sh` creates it and the `.env` discovery block.
- Device credentials live ONLY in `.env` (`DISCOVERY_*` vars, gitignored).

## Add a device or subnet

Edit `agent.yaml` policies:

- `snmp_discovery` targets take `host:` entries — single IPs, CIDRs
  (`10.0.21.0/24`), or ranges (`10.0.21.1-50`).
- `device_discovery` scope is a list of `hostname:` entries (one per device;
  `driver:` optional — omit to let NAPALM auto-try eos/ios/nxos/junos/etc.).

Then re-run discovery (policies have no schedule → they run once per agent start):

```bash
docker compose restart orb-agent
```

Add a cron-style `schedule:` under a policy's `config:` for recurring scans.

## Where do I set the IPs to discover?

`discovery/agent.yaml`, in two independent places — edit both if you want a
device covered by both methods:

- `snmp_discovery` → `scope.targets[].host` — a single IP, a CIDR
  (`10.0.21.0/24`) or a range (`10.0.21.1-50`)
- `device_discovery` → `scope[].hostname` — one entry per device (SSH/NAPALM)

Then `docker compose restart orb-agent`. Policies without a `schedule` run once
per agent start, so a restart *is* the rescan. Add a cron expression under a
policy's `config` to make it recurring.

Remote sites use `collector/policies.yaml` instead — see
[collector/README.md](../collector/README.md).

## Gotchas learned

- **`diode-auth` restart-looping with "permission denied", and `orb-agent`
  restarting with it.** On Linux, bind mounts keep the host's file mode, and
  `diode-auth` runs as uid 100 rather than root — so config written under a
  restrictive umask (0077) is unreadable inside the container. `orb-agent` then
  restart-loops as a *symptom*, because it cannot authenticate while auth is
  down. `scripts/init-dev-env.sh` now fixes the modes; re-run it, then
  `docker compose up -d --force-recreate diode-auth-bootstrap diode-auth orb-agent`.
  macOS masks this entirely, so it only ever appears on Linux hosts.

- **Boot race**: the agent scans immediately on `up`. If NetBox is still
  migrating, the reconciler's applies fail (`connection refused`) and are NOT
  retried — just `docker compose restart orb-agent` once NetBox is healthy.
- **Auto-apply**: the OSS reconciler applies changesets directly
  (`AUTO_APPLY_CHANGESETS=true`); the browsable review queue is part of NetBox
  Labs' commercial console, not the OSS plugin. If review-before-apply is ever
  needed, the supported OSS path is the `netbox-branching` plugin (Diode
  Settings page can target a branch; review/merge in NetBox).
- 2960-class IOS emits a harmless `get_chassis_members` warning (not a stack).
- Discovered serials land on Device records → netbox_quotes lines auto-match to
  discovered hardware with no manual linking.

## Troubleshooting

```bash
docker compose logs orb-agent          # scan + ingest results
docker compose logs diode-reconciler   # changeset apply results
docker compose logs diode-auth-bootstrap  # OAuth client registration
```

Token smoke test:

```bash
curl -s -X POST http://127.0.0.1:8090/diode/auth/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "grant_type=client_credentials&client_id=diode-ingest&client_secret=$(grep '^DIODE_INGEST_CLIENT_SECRET=' .env | cut -d= -f2)&scope=diode:ingest"
```
