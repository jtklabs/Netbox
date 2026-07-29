# Runbook: 30-day AMI redeploy

The app node is disposable: DB lives in RDS, media in S3, secrets + SAML
material on the data disk. A redeploy is "launch new instance, attach disk,
boot" — zero manual steps once first-boot setup exists.

## Steps

1. **Build/refresh the AMI** (your compliance pipeline): Ubuntu 24 base +
   docker engine + compose plugin + this repo checked out at the pinned tag
   under `/opt/netbox` + `netbox-compose.service` enabled. App version pins do
   NOT change here (see RUNBOOK-upgrade.md — decoupled on purpose, D7).
2. **Stop the old instance** (do not terminate yet — it's the rollback).
3. **Detach the data disk** from the old instance; **attach to the new one**.
4. **Launch the new instance** with:
   - the instance profile granting S3 media bucket access (+ ECR pull if used)
   - `deploy/user-data.sh` as user-data (or rely on the enabled systemd unit)
5. Bootstrap runs automatically: mounts the disk by label `NETBOXDATA`, links
   secrets, pulls/builds images, `compose up`, gates on health.
6. **Verify** (5 minutes):
   - `curl -f https://nova.jtklabs.dev/netbox/login/` → 200
   - SSO login round-trip works; your user has expected rights
   - A discovered device page renders (data intact = RDS wiring correct)
   - Open a quote document (media = S3 wiring correct)
   - `docker compose ps` on the host: all services healthy
   - `tail /var/log/netbox-bootstrap.log` for warnings
7. **Terminate the old instance** after verification.

## Rollback

Old instance still exists until step 7: detach disk, re-attach to old
instance, start it. RDS was never touched.

## Notes

- Sessions survive because `SECRET_KEY` lives on the data disk and never
  changes. Users see at most the cutover window.
- In-flight background jobs (RQ) are lost at cutover; discovery re-runs on
  next agent start, housekeeping self-heals. Schedule the swap in a quiet
  window.
- First boot after a NetBox version bump runs migrations — expect the health
  gate to take several extra minutes that one time.
