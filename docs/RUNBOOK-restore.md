# Runbook: restore

## Database (RDS is the source of truth)

- **Point-in-time / snapshot restore**: restore in the RDS console to a NEW
  instance, then point `DB_HOST` in the data-disk `prod.env` at the new
  endpoint and `docker compose up -d --force-recreate netbox netbox-worker`.
- Take a manual snapshot before every NetBox version bump (see
  RUNBOOK-upgrade.md).

## Media (S3)

Enable bucket versioning once; a deleted/overwritten quote document is then
recoverable from the version history. Nothing app-side to do.

## Secrets / data disk

The data disk holds the only copies of `SECRET_KEY`, `API_TOKEN_PEPPER_1`,
SAML SP key, and diode client secrets. Snapshot the EBS volume on a schedule
(the 30-day cycle is a natural hook). Losing `SECRET_KEY` = all sessions
invalidated; losing the pepper = existing API tokens stop validating; losing
the SAML SP key = re-register the SP with the IdP.

## Fire-drill into dev (proves backups are real)

```bash
pg_dump -h <rds-endpoint> -U netbox -d netbox -Fc -f netbox.dump
docker compose exec -T postgres pg_restore -U netbox -d netbox --clean --if-exists < netbox.dump
docker compose restart netbox netbox-worker
```
Run once after Gate 6 and after any major schema change.
