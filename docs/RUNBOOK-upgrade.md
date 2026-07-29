# Runbook: version bump (monthly-ish, decoupled from the AMI cycle)

Pins live in: `Dockerfile-Plugins` (FROM), `compose/dev.yml` + `.env`
`VERSION`/image tags, `plugin_requirements.txt`, `.env` `DIODE_TAG`,
`compose/discovery.yml` (orb-agent tag). Record every change in `VERSIONS.md`.

## Procedure

1. **Check compatibility BEFORE bumping NetBox** — the deployment only moves
   when every plugin is ready:
   - netbox-lifecycle: releases/README
   - netboxlabs-diode-netbox-plugin: PyPI classifiers (declares max NetBox)
   - netbox_quotes (ours): we ARE the compat check — see step 2
2. **Spike in dev** (the pattern that caught 4.6 issues early):
   ```bash
   # bump pins in a branch, then
   docker compose build && docker compose down -v && docker compose up -d
   ```
   Watch migrations, run a page sweep (quotes pages, device pages, diode
   settings), restart orb-agent and confirm a discovery cycle applies.
3. **Known upcoming break**: NetBox 4.7 requires PostgreSQL 15+ (RDS must be
   upgraded first if it's on 14) and drops Redis < 6 (our Valkey 9 is fine).
4. Merge, push, and roll prod at the next convenient AMI window (or restart
   compose in place — a redeploy is not required for an app upgrade).
5. If ECR is in use: CI (or you) builds and pushes the new image tag first;
   update `PROD_IMAGE` in the data-disk `.env`.

## Rollback

Pins are git history: revert the commit, rebuild, `compose up -d`. NetBox
migrations are generally not reversible — take an RDS snapshot before rolling
prod onto a new NetBox minor.
