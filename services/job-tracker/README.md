# Live job tracker

A standalone, read-only wallboard for the Discovery plugin's device jobs. Run it
on another server; it reads NetBox's REST API and requires no NetBox changes.
Includes upgrades, software staging, upgrade audits, standards audits and
standards remediation. It does not display unrelated NetBox background/RQ jobs.

The lab instance is running at **https://10.50.10.162/jobs/**, on the existing
image-server host. See [deployment and maintenance details](DEPLOYMENT.md).

The page updates automatically every five seconds. One shared server poll feeds
all viewers; the NetBox token never goes to their browsers. No database, Node
build, package installation, external fonts, or CDN is needed.

## Run on another server

Copy this directory to that server, then:

```bash
cp .env.example .env
chmod 600 .env
# Edit .env with the NetBox base URL and read-only API token.
docker compose --env-file .env -f compose.yaml up -d --build
```

Open `http://127.0.0.1:8090` on that server, or put its existing HTTPS reverse
proxy in front of port 8090. For direct LAN access, set `TRACKER_BIND_ADDRESS`
to the server's internal IP or `0.0.0.0`, then run the Compose command again.
`TRACKER_PUBLIC_PORT` changes the published port. Serve the tracker at the root
of its own hostname; if proxying under a subpath, retain the trailing slash and
strip the prefix before forwarding requests.

Use your existing internal access controls or authenticated reverse proxy for
viewers. The tracker has no login of its own: anyone who can reach it can read
the displayed device names, statuses, messages and recent events. API access is
limited to the token's object permissions. Do not expose the service publicly.

The token needs read access to:

- `netbox_discovery.view_upgradejob`
- `netbox_discovery.view_upgradegroup`
- `dcim.view_device`

Give it visibility into the entire maintenance scope so group activity and
dependencies are not hidden by object restrictions. Use a read-only token.
The service makes only GET requests to `api/plugins/discovery/upgrade-jobs/`,
`api/plugins/discovery/upgrade-groups/`, and `api/dcim/devices/`.
Set `NETBOX_AUTH_SCHEME=Bearer` if required by your token type; default is `Token`.

For an internal certificate authority, set `NETBOX_CA_FILE` to a PEM trust
bundle on the tracker server. In Docker, mount that file read-only and set the
variable to its container path. TLS verification stays enabled. API redirects
are rejected; configure the final NetBox URL, including `/netbox` if used.

### Without Docker

Python 3.10+ is sufficient. Supply the same settings through the service
manager's environment (the script does not load `.env` itself):

```bash
python3 server.py --host 0.0.0.0 --port 8090
```

Run under your existing process supervisor and reverse proxy. For systemd,
use this directory as `WorkingDirectory`, its `.env` as `EnvironmentFile`, and
`/usr/bin/python3 /path/to/job-tracker/server.py --host 127.0.0.1` as `ExecStart`.
Use a dedicated unprivileged user and `Restart=on-failure`.

For encrypted credentials, the included `netbox-job-tracker.service.example`
uses systemd's `LoadCredentialEncrypted`. Store only the encrypted token in
`/etc/credstore.encrypted/netbox-job-tracker-token`, encrypted with the credential
name `netbox-token`. The service reads the decrypted credential from systemd's
in-memory credential directory at startup. Keep nonsecret settings in
`/etc/netbox-job-tracker/config`; omit `NETBOX_TOKEN` from that file. An explicitly
set `NETBOX_TOKEN` takes precedence. This option requires a systemd version
supporting encrypted credentials.

## What the page shows

- Overall and per-wave progress, colored by successful, warning, failed, and
  cancelled/expired outcomes. A finished job is not necessarily successful.
  Recovery-required jobs remain unfinished because they still fence devices.
- Current stage and message, hold reason, stale heartbeat and recovery flags,
  worker name (or ID), and last heartbeat. An animated activity bar means work is in
  progress; the API does not expose a reliable percentage within a job.
- Active and pending jobs first, arranged by actual upgrade group, then batch,
  then saved wave. Groups, batches, waves and rows prioritize running/prechecks,
  recovery, pending, and held work. Saved wave numbers remain unchanged even
  when the current activity moves a later wave to the top. Equal-priority waves
  sort by saved wave number; rows use scheduled time and job ID as tie-breakers.
- Finished jobs are collapsed by default in a separate expandable history area.
  Its open/closed state survives live updates. Failed, warning, expired and
  cancelled outcomes remain visible in its summary and the attention total.
- Distinct text/color badges identify software upgrades, software staging,
  upgrade audits, standards audits, and standards remediation.
- Frozen job groups and dependencies, with current group concurrency limits
  and activity across **all tracked batches**, even when filters hide rows.
  A job in multiple groups appears in each group, with its other memberships
  listed. Overall totals count each job once. Jobs with no saved upgrade group
  appear in a clearly labeled ungrouped section.
- Device details with the latest 12 events and a link to the job in NetBox.
- Each upgrade group has its own completion bar and concurrency limit. Waves
  are visually nested inside their group and batch, with per-wave progress.
  Live-section progress includes finished jobs that are collapsed in history.
- Batch, operation, site, text, and attention filters. Filters apply to both
  totals and rows and are stored in the URL for bookmarking a wallboard view.
- Full-screen mode, responsive layout, keyboard-accessible details, and reduced
  motion support. Times use each viewer's browser timezone.

The page uses the actual group names saved on each job. It does not introduce
core/access/distribution tier headings or use role tiers as display sort keys.
Group membership, wave and dependencies come from the scheduling snapshot;
site labels come from current inventory and group limits are live definitions.
Display terminology uses “software staging,” including older “prestage” labels
in descriptions and messages; the underlying NetBox records are unchanged.

Waves are a plan, not a strict global barrier: NetBox workers can start any
eligible job according to dependency, capacity, window, and failure checks.
The tracker displays actual status without trying to reschedule or predict
eligibility. Dependencies outside the batch are labeled as such in details;
they can still matter to cross-batch active-device exclusion in the scheduler.

## Coverage, freshness and load

Every unfinished batch is tracked, including old held and recovery jobs. The
service also discovers up to `TRACKER_RECENT_BATCHES` (default 8) recent batches
from the newest 100 jobs by scheduled time. It follows all pages for every
selected batch, including its finished jobs, so progress denominators stay
complete. This is an operational wallboard, not an archive browser; at high
volume the newest 100 jobs may represent fewer than eight recent batches.

Polling is sequential and shared across viewers. Each cycle requests active
jobs, recent jobs, full selected batches, their devices in chunks of 100, and
group definitions. `TRACKER_POLL_SECONDS` is the delay between cycles (2–300
seconds); a full fetch can take longer. The browser polls the shared snapshot
at the same interval, so propagation can take approximately two intervals plus
API request time. Increase the interval for large installations.

If any request fails, the previous complete snapshot stays visible with an
error banner. No partial snapshot replaces it. The UI also flags snapshots
older than three polling intervals (at least 30 seconds), including a stalled
server refresh, and pauses activity animations while disconnected.
`/healthz` returns 503 until a successful fetch or when the feed is unhealthy.

The proxy returns an allowlist of display fields; job profiles, standards
snapshots, raw result summaries, and credentials are never included. Stage
messages and recent event messages are displayed as supplied by NetBox.

## Preview and checks

```bash
python3 server.py --demo
# Open http://127.0.0.1:8090 (clearly labeled simulated data).
python3 -m unittest discover -s tests -v
node --test tests/test_ui.cjs
```

Demo mode does not use an API token or contact NetBox. One precheck message
changes every 15 seconds to demonstrate automatic updates.
