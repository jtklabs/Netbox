# Lab deployment

Deployed October 14, 2026.

- URL: **https://10.50.10.162/jobs/**
- Host: `netbox-lab`, the same Hyper-V VM whose HTTPS proxy serves `/images/`.
- Application: separate Python service at `/opt/netbox-job-tracker`.
- Service: `netbox-job-tracker.service`, enabled at boot, with an unprivileged
  dynamic user and automatic restart on failure.
- Backend listener: `192.168.249.1:8090`, the existing Docker bridge gateway;
  not bound to the host's LAN address.
- Nonsecret settings: `/etc/netbox-job-tracker/config`.
- API: `https://10.50.10.162/netbox/api/`, certificate verification enabled.
- NetBox account: `netbox-job-tracker`, unusable login password, no superuser
  rights, view permissions only for device jobs, upgrade groups, and devices.
- Token: read-only, restricted to the host's LAN/bridge/loopback addresses.
  Stored encrypted at `/etc/credstore.encrypted/netbox-job-tracker-token`;
  systemd makes the decrypted credential available in memory at service startup.
  No API token is stored in this repository or in the service environment file.

## HTTPS proxy

The existing `netbox-lab-dev-proxy-1` container routes `/jobs/` to the standalone
service and strips the prefix. The route has the same private-network allowlist
as `/images/`: lab LAN, Docker bridge subnet, Tailscale range, and localhost.
Only GET/HEAD are allowed by the proxy. This is a private wallboard, with no
separate viewer login. It uses the existing lab certificate.

Persistent configuration:
`/opt/netbox/compose/lab-proxy.conf.template`.

The proxy was syntax-checked and gracefully reloaded. NetBox, the NetBox worker,
the proxy container, and the scheduled upgrade process were not restarted.
The original proxy template and container start times were saved at:
`/opt/netbox/lab-backups/job-tracker-20261015T010705Z/`.

## Operation

Run these commands on the host:

```bash
sudo systemctl status netbox-job-tracker
sudo journalctl -u netbox-job-tracker -n 50 --no-pager
curl --noproxy '*' http://192.168.249.1:8090/healthz
```

To update the application, copy `server.py`, `demo.py`, and `static/` into
`/opt/netbox-job-tracker`, retaining world-readable source files and traversable
application directories. Then restart **only** `netbox-job-tracker`. It reads
the existing encrypted credential on startup. No NetBox rebuild is needed.

The page uses actual saved upgrade groups and their batch/wave structure.
Role-tier names are not shown as headings or used to sort the live view.
Active and pending work is shown first; finished history is collapsed by
default and stays open when expanded during automatic updates. Software
upgrades, software staging, upgrade audits, standards audits, and standards
remediation have distinct job-type badges.

## Verification

Certificate-validated HTTPS returned 200 for the page, JavaScript, CSS, live
snapshot, and health endpoint. The feed returned eight real jobs and the
`F5 Unit 3 and 4` group with concurrency limit one. NetBox login and the existing
image endpoint still returned 200. The existing containers retained their
original start times. The local automated checks passed (19 tests).

The in-app browser rejected the existing self-signed lab certificate; it was
not bypassed. HTTPS was instead validated against the public certificate
retrieved through the existing SSH connection.

## Remove the wallboard

Stop and disable `netbox-job-tracker.service`, remove only the `/jobs` and
`/jobs/` locations from the proxy template, regenerate the proxy's effective
configuration, test it with `nginx -t`, and gracefully reload nginx. Revoke the
dedicated NetBox tracker token/account when no longer needed. Preserve any
later unrelated proxy edits; do not blindly restore the whole backup template.

## October 14 display update

The grouping, priority order, terminology and collapsed history were updated
in static assets only; the tracker, NetBox and upgrade worker did not require a
restart. The local simulated browser preview verified the layout and history
behavior; the deployed page is checked through certificate-validated HTTPS.
Existing viewers need one page reload to receive new frontend code.

Display-update rollback files:
`/var/backups/netbox-job-tracker/display-20261015T013201Z/`.
All three published assets matched the tested source over HTTPS. The live feed
remained healthy; tracker PID and existing container start times were unchanged.
The updated checks passed: 14 backend tests and 9 display-logic tests.

## Batch visibility and runtime update

Completed devices remain in the active view until every job in their batch is
terminal, including jobs hidden by the current filters. Only whole finished
batches move into collapsed history. Rows show total runtime beside current
step duration. Poller-reported elapsed time is preferred; otherwise runtime
uses the job start and completion timestamps, explicitly labeled.
