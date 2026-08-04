# Runbook: version bump (monthly-ish, decoupled from the AMI cycle)

The NetBox tag is pinned in **five** places and all of them must move
together — a build tagged `netbox-custom:<new>` from a `FROM <old>` base is a
silent downgrade, and missing the prod file ships the old version to prod:

| File | Occurrences | What it sets |
|---|---|---|
| `Dockerfile-Plugins` | 1 | `FROM` — the base image actually built against |
| `compose/dev.yml` | 2 | dev `netbox` + `netbox-worker` image tag |
| `compose/prod.yml` | 2 | prod `netbox` + `netbox-worker` image tag |
| `.env.example` | 1 | `VERSION` (seeds a new `.env`) |

Verify with `grep -rn '<old-tag>' --include='*.yml' --include='Dockerfile*' --include='*.example' .`
returning nothing. Other pins: `plugin_requirements.txt`, `.env` `DIODE_TAG`,
`compose/discovery.yml` (orb-agent tag). Record every change in `VERSIONS.md`.

An existing `.env` is **not** rewritten by `scripts/init-dev-env.sh` (it only
adds missing keys), so a running dev host keeps a stale `VERSION`. That is inert
— both overlays set the image tag explicitly — but update it to avoid confusion.

## Procedure

1. **Check compatibility BEFORE bumping NetBox** — the deployment only moves
   when every plugin is ready:
   - netboxlabs-diode-netbox-plugin: the compatibility table in its PyPI
     description maps a NetBox floor to a plugin floor (the classifiers say
     nothing about NetBox, so read the description)
   - netbox_quotes / netbox_refresh (ours): we ARE the compat check — see
     step 2. Both declare `min_version` and no ceiling, so they will load
     against anything newer and fail at runtime rather than at startup
2. **Spike in dev** (the pattern that caught 4.6 issues early). Prefer an
   **in-place** upgrade over a greenfield one: it exercises the migration path
   prod will actually take, and `down -v` would destroy dev's discovered data.
   ```bash
   # bump pins in a branch, then
   docker exec netbox-postgres-1 pg_dump -U netbox netbox > /tmp/pre-upgrade.sql
   docker compose build netbox
   docker compose up -d --force-recreate netbox netbox-worker
   ```
   Watch migrations, run a page sweep (quotes pages, device pages, diode
   settings), restart orb-agent and confirm a discovery cycle applies. Use
   `down -v` only when you deliberately want a clean-install test.
3. **Known upcoming break**: NetBox 4.7 requires PostgreSQL 15+ (RDS must be
   upgraded first if it's on 14) and drops Redis < 6 (our Valkey 9 is fine).
4. Merge, push, and roll prod at the next convenient AMI window (or restart
   compose in place — a redeploy is not required for an app upgrade).
5. Rebuild the prod image with `./scripts/prod-build.sh` (it verifies every
   plugin loads) and bake it into the next AMI.

## A `git pull` does not update prod's settings

`prod.env`, `netbox.env` and `.env` live on the **data disk**
(`/mnt/data_disk/netbox-secrets`), outside the repo on purpose — that is why
they survive an AMI redeploy. Pulling updates `env/prod.env.example` and never
touches the file in use, so any setting added to the template since that disk
was prepared is silently absent and NetBox falls back to its own default.

`bootstrap.sh` now diffs the two on every boot and logs the missing keys, so
check `/var/log/netbox-bootstrap.log` (or `journalctl -u netbox-compose`) after
the first boot on a new version and reconcile anything it lists.

## Rollback

Pins are git history: revert the commit, rebuild, `compose up -d`. NetBox
migrations are generally not reversible — take an RDS snapshot before rolling
prod onto a new NetBox minor.
