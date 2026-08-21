# Scheduled jobs

Three kinds of work run on a schedule around this deployment, and each has its
own machinery. Nothing here is home-grown — NetBox ships a scheduler, and the
`netbox-worker` container that is already part of every stack (dev and prod)
is what executes everything below.

| What | Lives | Scheduled by |
|---|---|---|
| Data-update scripts (custom scripts) | `netbox-scripts/` in this repo | NetBox's Run Script form ("Schedule at" + "Recurs every N minutes") |
| Plugin jobs (Cisco EoX sync) | `netbox_refresh` | Self-scheduled system job once credentials are configured (weekly by default) |
| Collector-side sweeps (SNMP inventory, config audit) | The poller boxes | cron / systemd timers on those boxes — see `scripts/snmp-inventory/README.md`, "Two schedules" |

## Custom scripts: the lane for "scripts that update data"

NetBox custom scripts are Python classes NetBox runs inside the application —
full ORM access, a per-run log, changelog entries for everything they write,
and native scheduling. When somebody asks for "a script that fixes X up
nightly", this is the lane.

### Where scripts live, and why

Script files live in **`netbox-scripts/` in this repo**, which the compose
stack bind-mounts read-only at `/opt/netbox/netbox/scripts-repo`. They reach
NetBox through a **Data Source**, not by uploading:

An uploaded script is a file on the app node's local disk — and the prod node
is disposed of every 30 days, so uploads silently vanish at the next redeploy.
The data-source route survives: the DataSource row and the script's attachment
live in the database (RDS), and the file itself comes back with every `git
pull` because it is in the repo. Code review comes free, since a script change
is a commit like any other.

### One-time setup (per NetBox instance)

1. **Operations → Data Sources → Add**
   - Name: `netbox-scripts`
   - Type: `Local`
   - URL: `file:///opt/netbox/netbox/scripts-repo`
2. On the new data source, press **Sync**. Every `.py` in `netbox-scripts/`
   appears as a file.
3. **Customization → Scripts → Add**, pick the data source and the file.

After that, a changed script is: commit → pull → **Sync** on the data source
(NetBox re-reads the file; no container rebuild — scripts are not part of the
image).

### Running on a schedule

Open the script → **Run**:

* **Schedule at** — when the first run happens. Leave empty to run now.
* **Recurs every N minutes** — the cadence. `1440` is daily, `60` hourly.
  Empty means run once.
* **Commit changes** — unticked, NetBox rolls back everything the run wrote
  and keeps only the log. Schedule a dry run first when unsure.

Scheduled runs sit under **Operations → Jobs** in state `scheduled` until the
worker picks them up; their logs land in the same place afterwards. Two things
worth knowing:

* The worker must be up — `netbox-worker` in the compose stack. If jobs sit
  in `pending`/`scheduled` forever, that container is the first thing to
  check.
* Recurrence needs the *inputs* saved with the schedule, so a script meant
  for scheduling should have sensible defaults for every parameter (see
  `serial_hygiene.py`, the worked example).

### Writing one

Start from `netbox-scripts/serial_hygiene.py` — it is deliberately written as
the template: parameters, logging, idempotence, and the commit contract are
all demonstrated and commented. House rules:

* **Never manage transactions.** NetBox wraps the run; `commit` is its job.
* **Log every write.** The job log is the only trace a 3am run leaves.
* **Idempotent always.** A scheduled script reruns forever; the second run on
  unchanged data should log "nothing to do" and write nothing — partly
  because every write is a changelog entry, and a script that "updates" the
  same value nightly buries the changes that matter.

## Plugin jobs: the Cisco EoX sync

The Lifecycle plugin's EoX sync is a NetBox `JobRunner` (`netbox_refresh/
jobs.py`) and schedules itself: when `CISCO_CLIENT_ID` and
`CISCO_CLIENT_SECRET` are configured, the plugin registers the job as a NetBox
*system job* and the worker enqueues it at startup and re-enqueues after every
run — the same mechanism NetBox uses for its own housekeeping. The cadence is
`CISCO_SYNC_INTERVAL_MINUTES` (default 10080, weekly; 0 turns the schedule off
and leaves the Sync button). No shell, no manual enqueue.

Credentials live in `prod.env` on the data disk, or in Docker secret files
`/run/secrets/cisco_client_id` / `cisco_client_secret` (read first). Check a
new pair before the first sync — it gets a token and looks nothing up:

```bash
docker compose exec netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py sync_cisco_eol --check-auth
```

A dry run (`sync_cisco_eol --dry-run --limit 5`) then shows what the first
real sync would write. The scheduled entry is visible (and cancellable) under
Operations → Jobs like everything else. Every model the sync checks is stamped
with that date — Cisco answering "no EoL data" for current hardware records as
**EoL not announced**, not as a gap.

## Collector-side schedules

The SNMP inventory sweep and the IOS config audit run on the poller boxes,
outside NetBox, and are scheduled with cron or systemd timers **on those
boxes** — NetBox cannot reach out and run them. The reasoning and the
recommended cadences (nightly sweep, short-interval onboarding pass) are in
`scripts/snmp-inventory/README.md` under "Two schedules".
