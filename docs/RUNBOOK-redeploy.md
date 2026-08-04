# Runbook: 30-day AMI redeploy

The app node is disposable: DB lives in RDS, media in S3, and secrets on the
data disk. Apache/Mellon run on a separate server and are unaffected. A redeploy is "launch new instance, attach disk,
boot" — zero manual steps once first-boot setup exists.

## Steps

1. **Build/refresh the AMI** (your compliance pipeline): Ubuntu 24 base +
   docker engine + compose plugin + this repo checked out at the pinned tag
   under `/opt/netbox` + `netbox-compose.service` enabled. App version pins do
   NOT change here (see RUNBOOK-upgrade.md — decoupled on purpose, D7).
2. **Stop the old instance** (do not terminate yet — it's the rollback).
3. **Detach the data disk** from the old instance; **attach to the new one**.
4. **Launch the new instance** with:
   - the instance profile granting S3 media bucket access
   - `deploy/user-data.sh` as user-data (or rely on the enabled systemd unit)
5. Bootstrap runs automatically: mounts the disk by label `NETBOXDATA`, links
   secrets, pulls/builds images, `compose up`, gates on health.
6. **Verify** (5 minutes):
   - through Apache: `curl -sS -o /dev/null -w '%{http_code}\n' https://netbox.example.com/netbox/login/` → **302** to the IdP (behind Mellon an unauthenticated 200 would mean the path is unprotected)
   - on the instance: `curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: localhost' http://<private-ip>:8080/netbox/login/` → 200
   - SSO login round-trip works; your user has expected rights
   - A discovered device page renders (data intact = RDS wiring correct)
   - Open a quote document (media = S3 wiring correct)
   - `docker compose ps` on the host: all services healthy
   - `tail /var/log/netbox-bootstrap.log` for warnings
7. **Terminate the old instance** after verification.

## The backend address

This instance keeps the same private IP across redeploys, so `NETBOX_BACKEND`
in the Apache server's `netbox.conf` is written once and never touched again.
Confirm it after a redeploy (step 6 below) rather than assuming — if the
address ever does change, Apache is the only place that needs updating.

`BIND_ADDRESS` stays `0.0.0.0` regardless. It lives on the persistent data disk
and is read while Docker publishes the port, which can happen before a
secondary interface is fully attached; `0.0.0.0` cannot fail that race, a
specific address can.

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
