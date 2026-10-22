# F5 PHP mitigation job

Python 3.10+ job for the BIG-IP PHP configuration mitigation in
[F5 K000163821](https://my.f5.com/manage/s/article/K000163821), read on October 21, 2026.
It accepts one management IP or a CSV, connects over SSH, sends a JSON webhook
after each attempted device, and prints and saves the results. Credentials and
webhook configuration come from a local `.env` file.

The job installs `/etc/php.ini.d/K000163821.ini` with these values:

```ini
memory_limit = -1
post_max_size = 8M
upload_max_filesize = 7M
session.upload_progress.enabled = 0
```

It restarts `httpd`, plus `httpd_sam` automatically when that service is running.
Stopped or absent `httpd_sam` services are skipped unless explicitly required.
This is an INI
configuration file, not an executable PHP file. The job does not change WAF
policies, signatures, management access rules, or HA configuration.

F5 recommends testing these settings in staging before production. The file
does **not survive a TMOS upgrade**. Apply it to **each HA member and each blade**;
configuration synchronization does not substitute for updating local files.
List directly reachable blade IPs separately. Chassis fan-out is not implemented.
Service restarts can interrupt management access and applicable APM/SSLO services.
Review the current advisory before using the job; this tool does not determine
whether a particular TMOS version or module is vulnerable or patched.

## Setup

```bash
cd scripts/f5-php-mitigation
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Edit `.env`. Supply `F5_USERNAME` and either `F5_PASSWORD` or `F5_SSH_KEY`, plus
`WEBHOOK_URL`. The SSH account needs root privileges and access to Bash, either
as its login shell or through TMSH's `run /util bash`. The job probes both routes
with a read-only UID check and selects the working launcher before sending the
script. It does not change the login shell or elevate with sudo. Accounts denied
Bash access or returning a non-root UID fail at `shell_detect`, before dispatch.
An execution failure never triggers a retry through the other shell.
It requires the standard BIG-IP `php`, `bigstart`, GNU `timeout`, `pgrep`,
`pkill`, and Unix file utilities. Commands run as root; no sudo prompt is used.
It rejects a target without a BIG-IP `/VERSION` file or the PHP INI directory.

By default, `F5_HOST_KEY_POLICY=accept-new` accepts previously unknown SSH host
keys without prompting. A prepopulated `known_hosts` file is not required.
Existing keys in `~/.ssh/known_hosts`, or the file selected by `F5_KNOWN_HOSTS`,
are still checked: a changed key fails with `ssh_host_key_mismatch`. Newly
accepted keys stay in the connection's memory; the job never writes the host-key
file, including with concurrent workers. Unregistered keys are therefore trusted
again on each run.

Set `F5_HOST_KEY_POLICY=strict` to require an existing known-hosts file and a
matching entry for every device. Missing entries report `ssh_host_key_unknown`
before authentication. This policy is separate from username/password rejection
(`ssh_authentication_failed`). For nonstandard ports use the SSH `[address]:port`
known-hosts form. SSH agents and automatic key discovery are disabled so
authentication uses the supplied configuration.

All `.env` values are read from the specified file; ambient environment variables
do not override them. Paths in `.env` are relative to that file. Dollar signs in
passwords are literal. Quote values containing spaces or `#` using dotenv syntax.
`F5_SSH_KEY_PASSPHRASE` supports an encrypted private key.

## Run

Check one standard BIG-IP host (read-only on the device; still sends a webhook):

```bash
python mitigate.py --host 192.0.2.10 --check
```

Apply to one standard BIG-IP host:

```bash
python mitigate.py --host 192.0.2.10 --apply
```

No extra flag is needed to restart a running `httpd_sam`. For known **APM or
SSLO** targets, declare that the service is expected so a stopped/absent service
fails the precheck instead of being skipped:

```bash
python mitigate.py --host 192.0.2.20 --require-httpd-sam --apply
```

For multiple hosts:

```bash
python mitigate.py --csv devices.csv --check
python mitigate.py --csv devices.csv --apply
```

Process up to five devices at a time using a thread pool:

```bash
python mitigate.py --csv devices.csv --apply --workers 5
```

Set `F5_WORKERS=5` in `.env` to make that the default; `--workers` overrides it.
The allowed range is 1–32, and the default is 1. Each worker owns its SSH and HTTP
connections and handles one device through notification before taking another.
Report writes and console lines are serialized. Webhooks from separate workers
can arrive concurrently and in any order.

SSH connection and authentication have a separate limit:
`F5_SSH_LOGIN_WORKERS=1` by default, including when an older `.env` omits it.
For example, `F5_WORKERS=5` with `F5_SSH_LOGIN_WORKERS=1` allows five devices
in progress but only one SSH login at a time. Once a login completes, its
shell commands, service checks, and webhook can overlap with other devices.
The login limit accepts 1–32 and applies within one job process; separate
invocations do not share it. Slot waiting does not consume `F5_CONNECT_TIMEOUT`.
The effective limit is printed at startup and saved as `ssh_login_workers`
in the JSON run report.

This reduces simultaneous authentication requests when CSV batches fail but
individual hosts work. It does not establish that the credentials or a shared
authentication backend are the cause. Authentication rejections are not retried;
each device still reports its original failure and sends one completion event.
If failures persist with serialized logins, compare the exact error/stage,
CSV port overrides, and the same explicit `--env-file` with `--host`.

Use concurrent workers for independent devices. The job does not discover or
serialize HA peers or blades belonging to the same chassis. Run those targets
with `--workers 1` or separate them into batches to avoid simultaneous restarts.

The only required CSV column is `host`; accepted columns are:

```csv
host,name,port,httpd_sam
192.0.2.10,bigip-a,22,auto
192.0.2.11,bigip-b,22,auto
192.0.2.20,bigip-apm,22,true
```

`host` must be an IPv4 or IPv6 address. Repeated IPs are deduplicated after
normalization (including equivalent IPv6 spellings). Each unique IP runs once,
in first-seen order, using the first row's name, port, and `httpd_sam` setting;
later rows for that IP are ignored even if those settings differ. Target counts,
webhooks, and device report entries reflect unique IPs. Every row is validated,
so malformed rows still fail before any device is contacted. Names are optional labels.
Empty/missing `port` uses `--port` (default 22). The `httpd_sam` column accepts:

| Value | Behavior |
| --- | --- |
| `auto` or `false` | Restart if running; skip if stopped or absent |
| `true` | Expect the service to be running; fail before changing files if stopped/absent |
| Empty/missing | Use `--require-httpd-sam` if supplied; otherwise automatic |

An explicit CSV value overrides the flag. `--httpd-sam` remains an alias for
`--require-httpd-sam`. **Behavior change:** an old CSV value of `false` now means
automatic selection; it no longer excludes a running `httpd_sam` from restart.

The job queries `bigstart status httpd` and `bigstart status httpd_sam` directly
and recognizes both daemontools and SysV output. The aggregate service list can
omit these services on some releases. A failed, ambiguous,
or unrecognized discovery result fails the precheck rather than silently skipping
the service. A check requires `httpd` to be running. Apply can recover a stopped
or unresponsive `httpd` after acquiring the device lock and before installing
the file. Automatic selection detects service
state, not module provisioning: mark known APM/SSLO hosts as required to catch a
service that should be running but is down. Skipping a stopped service does not
prove it is unused. The job never starts an inactive optional service.

`--check` is the default. Use `--env-file /path/to/.env` and
`--report-dir /path/to/reports` for unattended jobs. No interactive prompts occur.

## Execution and recovery

Hosts are processed up to the configured worker limit. Each apply acquires a per-device directory
lock, backs up any existing target under a private `/var/tmp/K000163821.*`
directory, and installs the supplied file using an atomic rename. If there was
no original file, the backup directory records `original_absent`. Only this INI
file is managed. Backups are retained on the device and their path is reported.

Verification checks the exact file SHA-256, PHP CLI's normal INI scan and four
effective values, and `bigstart` running status for each selected service.
This does not perform an exploit test, inspect Apache's PHP configuration over
HTTP, or test application transactions. Validate management and APM/SSLO behavior
in your staging environment before a production rollout.

Unchanged file bytes skip installation, but an explicit `--apply` still restarts
the selected services. This ensures an earlier interrupted reload is not silently
treated as complete. Use `--check` when only verification is needed.

Restart and start commands have a 45-second limit, followed by up to 30 seconds
of running-state checks. Status commands are bounded too. If the `httpd` restart
fails, times out, or fails its status checks, the job:

1. Attempts `bigstart stop httpd` with a 15-second limit.
2. Runs `pkill -KILL -x httpd`, targeting the exact process name. No matching
   process is acceptable; the service may already have stopped.
3. Waits up to 10 seconds for those processes to disappear. A kill error or
   surviving process fails recovery; it does not start over a surviving daemon.
4. Runs `bigstart start httpd` and verifies its running status before continuing.

GNU `timeout` allows five additional seconds before killing a service-control
command that ignores termination. Forced termination is limited to `httpd`;
`httpd_sam` uses a bounded normal restart. Recovery can interrupt management
sessions. F5 documents killing remaining httpd processes and starting the service
as a workaround for [bug 775845](https://cdn.f5.com/product/bugtracker/ID775845.html),
which concerns a failed restart through iControl REST; this job does not diagnose
that specific bug.

The same recovery logic is used during rollback. A stopped `httpd` at the start
of `--apply` is recovered under the device lock before any INI changes. If that
recovery fails, the job stops scheduling new devices. `--check` never starts,
stops, or kills a service. A stale lock from an interrupted earlier run still
requires inspection as described below.

`F5_COMMAND_TIMEOUT` defaults to 900 seconds. Apply uses at least 900 seconds,
even with an older `.env` set to 300, so bounded recovery and rollback have time
to finish. Check uses the configured timeout directly. A lost SSH connection
can still make the result uncertain. Running status is not an HTTP transaction
test and does not establish that every GUI or application endpoint responds.

When a detected failure occurs after installing a changed file, the job attempts
to restore the original file (or remove the newly created file) and restart the
selected services. `failed_rolled_back` means that restoration and running-state
checks completed. `failed_rollback_incomplete` needs manual recovery. If the file
was already identical, a service failure is reported as `failed`; there is no
earlier file to restore in that run.

An SSH disconnect or timeout after dispatch is reported as `unknown`: the remote
job may still be running. The job never retries device changes automatically.
Ordinary device failures and webhook failures do not prevent later devices from
being processed. An unknown device outcome, incomplete rollback, unrecovered
service restart/verification failure, interruption, or report write failure stops
new devices from being scheduled. Already-started workers finish and send their
webhooks; they cannot be recalled once a remote change has started. The report
includes the worker limit and unprocessed count. Ctrl+C stops new scheduling and
waits for in-flight operations and notifications before printing the final report
and returning 130. This can take up to their configured timeouts. This tool does
not coordinate HA failover or discover peers.

Before retrying an unknown result, inspect the device's file, selected services,
backup directory, and any running job. A killed process may leave
`/var/run/K000163821.lock`; remove it only after verifying no job is active.
Do not automatically expire this lock. A manual restore should copy the recorded
`original.ini` back into place and restart the same services; if
`original_absent` was recorded, remove only the mitigation INI file instead.

## Webhook and reports

Both the destination and bearer token are read from the selected `.env` file:

```dotenv
WEBHOOK_URL=https://automation.example.net/webhooks/f5-mitigation
WEBHOOK_BEARER_TOKEN='your-shared-receiver-secret'
WEBHOOK_TIMEOUT=15
WEBHOOK_ATTEMPTS=3
```

The receiver must accept a generic JSON POST over HTTPS and return a 2xx status.
The token is sent in the Authorization header, never in the URL or JSON body.
An empty token omits that header. Set `WEBHOOK_CA_BUNDLE` for a private CA.
TLS verification is always enabled.
Redirects are rejected. Ambient HTTP proxy and `.netrc` settings are not used.
Slack/Teams-specific cards or message envelopes are not generated.

The webhook runs after each attempted device, including failures and checks,
without waiting for the rest of the batch. It completes before that worker takes
another host. No event is sent for targets that were never started.

Request headers (the idempotency key equals the body's `event_id`):

```http
POST /webhooks/f5-mitigation HTTP/1.1
Content-Type: application/json
Authorization: Bearer your-shared-receiver-secret
Idempotency-Key: 0d0e1555-7a90-485e-a350-74e0b40f3e52
```

Complete successful apply example, also available in
[`examples/webhook-applied.json`](examples/webhook-applied.json):

```json
{
  "event": "f5.mitigation.device.completed",
  "schema_version": 1,
  "event_id": "0d0e1555-7a90-485e-a350-74e0b40f3e52",
  "run_id": "220fe43b-5269-46ba-9723-03dfde0ab54f",
  "advisory": "https://my.f5.com/manage/s/article/K000163821",
  "mode": "apply",
  "device": {"host": "192.0.2.10", "name": "bigip-a", "port": 22, "httpd_sam": false},
  "status": "applied",
  "changed": true,
  "services": ["httpd"],
  "httpd_sam_state": "absent",
  "httpd_sam_decision": "skip",
  "httpd_recovery": "not_needed",
  "httpd_recovery_attempts": 0,
  "httpd_force_killed": false,
  "stage": "complete",
  "previous_sha256": "absent",
  "sha256": "6a777b534aa1e2f6862ee3d40a42816745e111c024e9619eb18db2de958a97536",
  "backup": "/var/tmp/K000163821.aB12cD34",
  "login_shell": "tmsh",
  "script_exit_code": 0,
  "remote_exit_code": 0,
  "started_at": "2026-10-22T00:00:00+00:00",
  "finished_at": "2026-10-22T00:00:04+00:00",
  "duration_seconds": 4.0
}
```

The hash above is illustrative. A connection failure can contain fewer fields:

```json
{
  "event": "f5.mitigation.device.completed",
  "schema_version": 1,
  "event_id": "aee1aaf4-de4d-4de0-8e90-011e5848ae53",
  "run_id": "220fe43b-5269-46ba-9723-03dfde0ab54f",
  "advisory": "https://my.f5.com/manage/s/article/K000163821",
  "mode": "apply",
  "device": {"host": "192.0.2.11", "name": "bigip-b", "port": 22, "httpd_sam": false},
  "started_at": "2026-10-22T00:00:00+00:00",
  "finished_at": "2026-10-22T00:00:01+00:00",
  "duration_seconds": 1.0,
  "status": "failed",
  "changed": null,
  "stage": "connect",
  "error": "ssh_authentication_failed"
}
```

This example is also in [`examples/webhook-failed.json`](examples/webhook-failed.json).

### Receiver contract

| Field | Type / interpretation |
| --- | --- |
| `event`, `schema_version` | Always `f5.mitigation.device.completed` and integer `1` |
| `event_id` | UUID string; unique per device attempt, unchanged across delivery retries |
| `run_id` | UUID string shared by all device attempts in one invocation |
| `advisory` | Advisory URL identifying this mitigation |
| `mode` | `apply` or `check`; a check result must not be treated as a deployment |
| `device` | Object with `host` (IP string), `name` (possibly empty string), `port` (integer), `httpd_sam` (boolean expectation flag) |
| `started_at`, `finished_at` | UTC ISO 8601 timestamps with offset; these describe device work, not webhook delivery |
| `duration_seconds` | Nonnegative number; device execution time excluding webhook delivery |
| `status` | One of the statuses in the table below |
| `changed` | Boolean or `null` when no definitive change information is available; `true` can also occur after rollback was attempted |
| `stage` | Last observed operation stage; do not infer success from this alone |
| `error`, `failed_stage` | Optional sanitized error identifier and failure stage |
| `services` | Optional array of selected services (`httpd`, optionally `httpd_sam`); present after successful discovery |
| `httpd_sam_state`, `httpd_sam_decision` | Optional discovery fields described below |
| `httpd_recovery` | Optional last recovery state: `not_needed`, `attempted`, `recovered`, or `failed`; overall `status` still determines the job outcome |
| `httpd_recovery_attempts` | Optional integer count across initial recovery, apply, and rollback (0–3) |
| `httpd_force_killed` | Optional boolean: true if an exact-name httpd SIGKILL command succeeded during this attempt |
| `previous_sha256` | Optional original file hash, or `absent` |
| `sha256` | Optional installed file hash after successful apply verification |
| `backup` | Optional directory on the device containing the backup/original-absence marker |
| `login_shell` | Optional detected command route: `bash` or `tmsh` |
| `script_exit_code` | Optional exit marker emitted by the inner Bash script; zero on completion, nonzero on a handled failure |
| `remote_exit_code` | Optional SSH command exit status; TMSH can return zero even when the inner script fails |

A complete terminal script marker is required to accept an outcome. A zero SSH
exit status alone is insufficient, including when TMSH launches Bash.

Fields through `stage` in the table are always present in emitted events.
Optional fields can be absent, especially when SSH fails. The payload does not
contain credentials, the destination URL, the bearer token, or delivery status.
`webhook` delivery metadata exists only in the local report: the sender cannot
include the eventual response to a request in that same request.

| `status` | Receiver meaning |
| --- | --- |
| `applied` | Apply completed, with file/PHP settings and selected services verified |
| `compliant` | Read-only check passed |
| `needs_change` | Read-only check found missing/different settings |
| `failed` | Attempt failed; inspect `error` and stage |
| `failed_rolled_back` | Apply failed; restoring the prior file and checking services completed |
| `failed_rollback_incomplete` | Apply failed and recovery needs attention |
| `unknown` | Remote outcome is uncertain; do not label mitigated or trigger automatic reapply |

Receiver implementation steps:

1. Verify the bearer token before accepting the request, using a constant-time
   comparison against the receiver's configured secret. Do not log the header.
2. Validate JSON, the event name, schema version, and required fields. Allow
   additional fields for compatible future additions.
3. Atomically store the event keyed by `event_id` (a unique database constraint
   works across concurrent requests). If already stored, return 200 or 204
   without repeating downstream actions. Do not deduplicate by host or run ID.
4. Return 200, 202, or 204 promptly after durable storage/enqueueing. A 2xx body is
   ignored by the sender. Perform slow downstream work asynchronously.
5. Group results by `run_id` and device IP for display. Requests can arrive out
   of order. There is no batch-complete webhook or expected-target count in these
   per-device events; do not infer that the whole batch is complete from one event.

Return 401/403 for a bad token and 400 for an invalid payload. Return 429 or 503
for transient receiver failures when a retry is appropriate. Do not return 2xx
before accepting responsibility for the event. A delivered webhook reports an
outcome; receiving it must never cause the mitigation job to run again.

Network errors, HTTP 429, and HTTP 5xx are retried with bounded backoff (default
three attempts, maximum five). Retries reuse `event_id` and the `Idempotency-Key`
header. The receiver should deduplicate on that ID because a timed-out request
may already have been accepted. Other HTTP errors, including redirects, are not
retried. A delivery failure never triggers another mitigation attempt.

When discovery is reached, the printed report and webhook include
`httpd_sam_state` (`running`, `stopped`, `absent`, or `unknown`) and
`httpd_sam_decision` (`restart`, `check`, `skip`, or `blocked`). These record the
precheck decision; the overall result states whether the operation completed.
An `applied` result with decision `restart` confirms the selected service was
restarted and its running state verified. `--check` never restarts either service.

The JSON report is saved with mode 0600 before delivery and updated afterward.
It includes each payload plus a separate `webhook` delivery result. Credentials,
the webhook URL/token, and raw remote output are excluded. Device IPs, labels,
timestamps, status and backup paths are included. A process crash may leave a
`pending` delivery result; use the stored event to replay only the notification.
There is no automatic persistent webhook replay queue.

The terminal prints one completion line per device and a final table. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Every target applied/compliant and every webhook delivered |
| 1 | A device needs change/failed/is unknown, delivery failed, or report write failed |
| 2 | Invalid setup or arguments; no devices contacted |
| 130 | Interrupted; inspect any uncertain device outcome |

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests use mocked SSH/webhooks and a temporary filesystem with simulated BIG-IP
utilities for installation, rollback, locking and service failures. They never
connect to real devices or send external webhooks.

Live read-only validation on f5-lab03 (October 22, 2026 UTC) completed through its
root Bash SSH login and through the device's real TMSH interpreter. Both returned
`needs_change`, detected the absent mitigation file, and skipped unprovisioned
`httpd_sam`. Requiring that stopped service returned the expected preflight
failure through both routes, including when TMSH returned SSH exit code zero.
The TMSH interpreter test wraps SSH commands in `tmsh -c`; it does
not establish password authentication for a separate TMSH login account. Live
installation, rollback, service restarts, and external webhook delivery have not
been exercised as part of this validation.

Additional read-only CSV validation on October 22, 2026 used all four lab F5s
with duplicate rows, first with one worker and then with four workers. All four
connected using root SSH keys and returned `needs_change`. A four-worker run
with a nonexistent known-hosts file also reached all four devices using the
`accept-new` default. Notification calls were intercepted locally; no external
webhooks, configuration writes, or service restarts occurred. Separate password
CSV checks reached three devices; lab01 rejected its saved password with both
CSV and `--host`, independently of host-key policy.

Hung-restart recovery is covered by simulated appliances with blocked restart
and stop subprocesses, failed health checks, failed SIGKILL/start operations,
surviving processes, initially stopped httpd, and rollback. Live lab inspection
confirmed GNU timeout support and separate httpd/httpd_sam executable names.
No live forced kill or restart was performed for this recovery change.

The separate SSH login limit was validated with one- and four-worker read-only
CSV runs against all four lab F5s using root SSH keys. All eight device checks
reached inspection and returned `needs_change`, with the effective login limit
of one recorded in each run report. Webhooks were intercepted locally. Tests
also verify parallel remote work after serialized logins, the configured login
limit, and slot release on authentication, timeout, and host-key failures.
The reported concurrent password-authentication failure has not been reproduced
in the lab; this change removes overlapping logins by default without claiming
to diagnose that failure.
