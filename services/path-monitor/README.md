# Path monitor

A standalone service for continuously tracing a configured list of IPv4/IPv6
destinations and reporting route changes. It lives alongside NetBox but has no
dependency on NetBox, its database, or its API. Deploy one instance at each
network vantage point you want to measure.

- Starts a traceroute to **each destination every second** by default. Each
  destination can use ICMP (default) or TCP to a configured port, such as 443.
- Records every finished trace, including per-TTL address sets, RTTs, missing
  replies, ICMP annotations, execution errors, start time and elapsed time.
- Keeps accepted multipath address sets per hop, rather than treating every
  alternating response as a route change.
- Stores timestamped baseline, path-change and reachability events with the
  before/after paths, confirming trace, and recent confirmation evidence.
- Provides an automatically updating dashboard, destination/time filters,
  expandable comparisons, raw trace history, paginated JSON and CSV export.
- History bars show the currently configured destination name before its IP
  address, including for existing records. Names are omitted for destinations
  no longer configured or without a distinct name. Live trace panels keep their
  individual scroll positions when observations refresh.
- Dark styling covers the dashboard and sign-in page. Expanded history/JSON
  sections keep their state and scroll position while new observations arrive;
  open records remain visible until closed, even after leaving the latest page.
- SQLite data and learned state survive container restarts. Default retention:
  **24 hours of raw traces, 90 days of events**.

## Deploy

On a Linux Docker host, copy this directory or clone the repository, then:

```bash
cd services/path-monitor
mkdir -p config
cp targets.example.json config/targets.json
# Replace the documentation IPs with your destination IPs. Remove or replace
# the example expected_hops entry too.
cp .env.example .env
chmod 600 .env
# Set your username, password and API token in .env.
docker compose -f compose.yaml up -d --build
```

Put your HTTPS reverse proxy in front of `127.0.0.1:8091`, then open its HTTPS
URL and sign in. Preserve the browser's `Host` header when proxying. Browser
cookies are Secure by default; plain HTTP is only suitable for local testing
with `PATH_MONITOR_COOKIE_SECURE=false`. For a different internal bind address:

```bash
PATH_MONITOR_BIND_ADDRESS=10.0.0.10 docker compose -f compose.yaml up -d
```

Use the actual server IP. `PATH_MONITOR_PUBLIC_PORT` changes the published port.
For persistent overrides put these settings in a local `.env` file. The root
NetBox Compose stack is independent; always use this directory's `compose.yaml`.
The default Compose bridge network measures paths from the container and can
add a bridge gateway/NAT at the start of the path. For the Linux host's exact
network view, use a local Compose override with `network_mode: host` and
`ports: !reset []` (requires Compose 2.24.4+); the server will then listen on
all host interfaces at port 8091. Choose the host/routing context intentionally.
IPv6 destinations require working IPv6 routing in the chosen container network.

The service uses Linux `traceroute -I -n` by default, or `-T -p PORT -n` for
TCP destinations, with only the `NET_RAW` capability;
it does not need privileged mode, host PID access, or NetBox credentials.
The container runs as root with all other capabilities removed so it can open
ICMP raw sockets and write the named data volume. The filesystem is read-only
apart from that volume. The web interface requires your username and password; all
data APIs and CSV exports require a browser session or integration key. The
service refuses to start without the three required credentials, including in demo mode.
Use HTTPS and your existing internal network access controls.
Proxy to port 8091 at a hostname root, or strip a subpath prefix and retain its
trailing slash. There are no external scripts, fonts or CDNs.

### Apache alongside Checkmk

Include [`apache-path-monitor.conf`](apache-path-monitor.conf) **inside your
existing HTTPS VirtualHost** that answers requests to the server IP. It proxies
only `/path-monitor/` to `http://127.0.0.1:8091/` and redirects `/path-monitor`
to the required trailing slash. Place its ProxyPass rules before any catch-all
`ProxyPass "/"` rule. Keep existing Checkmk routes and authentication directives.
The prefix must not already be used as a Checkmk site name.
If an existing redirect/rewrite sends every URL to Checkmk, exclude
`/path-monitor` and `/path-monitor/` from that rule. A redirect matching only
the root URL (`^/$`) can stay as it is.

Enable `alias`, `proxy` and `proxy_http` modules (plus your existing `ssl` module).
On Debian/Ubuntu, use `sudo a2enmod alias proxy proxy_http`, then validate and
reload with `sudo apache2ctl configtest && sudo systemctl reload apache2`.
On RHEL-family systems, validate and reload with
`sudo httpd -t && sudo systemctl reload httpd`; the proxy modules must be loaded.

Keep `PATH_MONITOR_COOKIE_SECURE=true`. Open `https://SERVER_IP/path-monitor/`;
the certificate served by that VirtualHost must be trusted and include the IP
address in its subject alternative names for warning-free IP access. Preserving
Host is required; X-Forwarded-Proto is not required by this app. Do not rewrite
the app's cookie Path: secure `__Host-` cookies require `Path=/`. Do not override
its `Referrer-Policy: same-origin` response with `no-referrer`.

The API is available under the same prefix, for example
`https://SERVER_IP/path-monitor/api/status`, with the usual Bearer token.
If Apache runs in a different container/host, replace the upstream address with
one Apache can reach; `127.0.0.1` refers to Apache's own network namespace.

## Web and integration authentication

Set these values in `services/path-monitor/.env` (gitignored):

```dotenv
PATH_MONITOR_USERNAME=jason
PATH_MONITOR_PASSWORD='your-private-password'
PATH_MONITOR_API_TOKEN='your-random-token-of-at-least-32-characters'
PATH_MONITOR_COOKIE_SECURE=true
```

Use your own password and token; the values above only show the format. A token
can be generated locally with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Single-quote values containing `$` or `#` so Compose treats them literally. Compose
reads `.env` and supplies these three credentials to the container; it does not
mount the file or copy it into the image. No extra auth file or account setup is
needed. For native execution, supply the same environment variables through
your service manager (for example, systemd's `EnvironmentFile`). The Python
server reads its environment and does not source a dotenv file itself.

To change the login or rotate the token, edit `.env` and run:

```bash
docker compose -f compose.yaml up -d --force-recreate
```

Recreating the container loads the new values and invalidates browser sessions;
`docker compose restart` alone does not load changed environment values. The
single login and token have read access to all destinations and retained history.
The server derives a salted password hash and token digest in memory; it never
returns either credential to the browser, API responses or logs.

Browser sessions last at most eight hours and end on sign-out or server restart.
Cookies use HttpOnly, Secure and SameSite=Strict. Login/logout have CSRF protection
and failed logins show a generic error. Login attempts are throttled by source IP.
Behind a reverse proxy, clients share the proxy's source IP for this limit;
forwarded-IP headers are not trusted. Use `PATH_MONITOR_COOKIE_SECURE=false` for
local HTTP testing; keep the default `true` behind HTTPS. Preserve the browser's
`Host` header when proxying.
Sign in from the service's normal HTTP(S) browser origin; opaque-origin frames
or webviews that submit `Origin: null` are rejected.
The app uses `Referrer-Policy: same-origin` so ordinary login/logout forms retain
their origin. This supports HTTPS access by IP address as well as hostname;
the proxy must preserve the browser's `Host` header, including a non-default
port if used. Cross-origin requests do not receive referrer information.

Integration clients send `Authorization: Bearer KEY` over HTTPS. Missing or
invalid credentials return JSON HTTP 401 with a Bearer challenge. Keys in URLs
or query parameters are not accepted. The public `/healthz` endpoint returns
only `{"healthy": true}` or `{"healthy": false}`, with no destination, path,
settings or error details. Use authenticated `/api/status` for diagnostics.

The authenticated `GET /api/openapi.json` endpoint provides an OpenAPI 3.0
description of the data endpoints, filters, response shapes and pagination.
The existing read-only API is available to any HTTP client; for example:

```python
import json
from pathlib import Path
from urllib.request import Request, urlopen

key = Path("/secure/path/reporting.token").read_text().strip()  # Your configured API token.
request = Request(
    "https://paths.example.internal/api/events?target=10.20.30.40&limit=100",
    headers={"Authorization": f"Bearer {key}"},
)
with urlopen(request, timeout=30) as response:
    page = json.load(response)
# Read page['items']; pass page['next_before_id'] as before_id for older pages.
```

Keep the same filters while paging. All stored times are UTC Unix seconds;
the UI renders local time. Retention still applies to API results. For ongoing
imports, query overlapping time windows and deduplicate records within retained
history; pagination IDs are not permanent synchronization offsets.

## Specify destinations

Edit `config/targets.json`, for example:

```json
{
  "destinations": [
    {"name": "Primary data center", "address": "10.20.30.40"},
    {
      "name": "Remote office",
      "address": "10.60.70.80",
      "expected_hops": {"4": ["10.1.1.1", "10.1.1.2"]}
    },
    {"name": "IPv6 endpoint", "address": "2001:db8::80"},
    {"name": "Kount", "address": "192.0.2.81", "protocol": "tcp", "port": 443}
  ]
}
```

Replace the example addresses before deploying. Addresses must be numeric IP
literals; hostnames, multicast, unspecified and scoped IPv6 addresses are
rejected. Up to 64 unique destinations can be configured. Names are optional.
`expected_hops` is optional: it maps hop numbers (TTL) to known alternatives.
These addresses stay accepted even when rarely observed. Other addresses at
that TTL are still monitored and reported.

Set `"protocol": "tcp", "port": 443` for destinations that need the equivalent
of `traceroute -T -p 443`. Omit both fields for the existing ICMP behavior.
Allowed protocols are `icmp` and `tcp`; TCP ports must be integers from 1 to
65535 and default to 443 if omitted. A port is only valid with `protocol: tcp`.
These options work with both IPv4 and IPv6. Other timing/retention settings
remain shared. New raw traces and event observations include `protocol` and
`port`, and the destination cards show the configured probe method.

TCP uses SYN probes rather than HTTPS requests; a destination reply does not
verify the web application. Intermediate hops still depend on ICMP time-exceeded
replies, so filtering can hide some hops even when TCP reaches the destination.
Changing a destination's protocol or port retains its learned state and can
produce path/reachability events because different probes may reveal different
routes. Existing retained observations remain available; older records without
protocol metadata used ICMP.

After editing, restart to validate and reload the file:

```bash
docker compose -f compose.yaml restart path-monitor
docker compose -f compose.yaml logs --tail=50 path-monitor
```

Adding a destination starts learning it. Removing one stops its probes but
retains its history and state until retention expires (state itself is kept).
Re-adding the same IP resumes its learned state. Existing destinations retain
their accepted sets across configuration changes; removing a pinned expected
address lets it age out under the normal grace rule. Run one service process
per data volume; SQLite is local storage, not a multi-replica shared backend.

## How multipath and changes work

1. **Learn:** the first 30 traces with at least one responding hop establish
   accepted address sets at each TTL. Both `A` and `B` become normal if they
   alternate at hop 4. The first accepted path is saved as a baseline event.
   Execution failures and completely silent traces do not advance learning.
2. **Confirm new addresses:** after learning, an unfamiliar address must appear
   in at least 3 of the last 10 traces. Once confirmed, a path-change event
   records the old and new accepted sets and the actual confirming observation.
   A single unusual reply remains in raw history without producing an event.
3. **Retain alternatives:** learned addresses remain accepted for 300 seconds
   after their last observation. When another confirmed address responds at
   the same TTL after that grace period, the old address is retired and another
   path-change event records the removal. Pinned router `expected_hops` never expire.
   A switch from `{A,B}` to `C` therefore first records `{A,B,C}`, then `{C}`
   once the old branches age out. The exact latest observation is always stored
   separately from the accepted sets.
4. **Ignore unknowns:** `*`, a silent hop, a command failure, and partial output
   from a killed trace cannot delete an accepted hop. A shorter route removes
   trailing hops only after repeated replies from the destination at its new
   TTL. Destination-TTL changes require 3 consecutive completed, unambiguous
   traces reaching the destination at the same new TTL (`confirmation_samples`
   controls this count). Alternating terminal TTLs, missing replies, duplicate
   destination replies at different TTLs, and command errors break this sequence.
5. **Track replies separately:** 3 consecutive completed traces with the same
   destination reply status confirm reached/no-reply transitions. This is an
   observation of destination replies, not a claim that an application is down. Command
   failures break the confirmation sequence and have no topology effect.

An address moving from hop 4 to hop 5 is a difference even when that IP is
already familiar. Path events explicitly list confirmed address movements,
including a return to an old position that is still in the accepted multipath
sets. Movements need 3 observations at the new position after the last confirmed
position change; they do not wait for the five-minute branch retirement period.
Both the first observation and confirming trace times are retained. Ordinary
A/B switching at the same TTL remains normal. Missing one reply from an address
seen at several TTLs is not enough to infer that it moved.

The destination is handled separately from router alternatives: accepted-path
comparisons show it only at its single confirmed terminal TTL, rather than
retaining it at several hops during the multipath grace period. A trace with
destination replies at multiple TTLs is ambiguous for terminal-hop changes;
its original hop addresses and RTTs are still recorded. Sustained shorter or
longer paths still produce a confirmed change, and ordinary router movement
detection is unchanged. Stored learned state from earlier versions is cleaned
on the next observation without generating a cleanup event. Previously recorded
raw traces and events are not rewritten.

### Investigating sites that stop replying

Probes and raw recording continue throughout a loss-of-reply interval. Filter
the destination and time range in the dashboard to examine the traces before,
during and after it. Each confirmed loss-of-reply event preserves the last actual
trace that reached the destination, the confirming partial/silent trace, the
farthest responding hop in that trace, and the accepted path at that point.
Recovery events compare the pre-loss successful trace with the recovery trace
and retain the first observed no-reply time. The pre-loss trace survives process
restarts and is embedded in the events, so it remains available for the event
retention period even after raw traces expire. Intermediate traces still use
the configured raw retention period; increase it for investigations covering
more than 24 hours.

These timestamps describe observed replies and confirmation, not an exact site
outage boundary. Collection gaps can occur within the interval. A responding
hop followed by `*` is not proof that the next router failed: ICMP filtering,
rate limiting and return routing can hide later replies. Correlate this history
with the site's actual reachability/application monitoring to establish when
the service was unavailable.

Ordinary A/B switching creates no path events once learned. A branch that first
appears later produces one addition event after confirmation, then becomes
normal too. Increase the grace period or pin expected alternatives for branches
that can be absent for more than five minutes. Silent TTLs retain their last
known sets; absence of evidence does not establish removal. The detector compares
sets at each TTL, not correlations between complete paths; accepted sets do not
prove every possible combination of addresses is a real end-to-end path.

This is repeated ICMP/TCP traceroute, not a Paris traceroute or exhaustive ECMP
enumerator. It reports the paths routers reveal to these probes. Filtering,
ICMP rate limiting, flow hashing and asymmetric return paths can affect what
is observed. RTTs and ICMP annotations are stored but do not trigger topology
events. Linux traceroute's concurrent probe and timeout flags are described in
the [upstream manual](https://man7.org/linux/man-pages/man8/traceroute.8.html).

## Timing, retention and sizing

Settings are optional keys in the top-level `settings` object:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `interval_seconds` | 1 | Start interval per destination (1–60 seconds) |
| `max_hops` | 30 | Maximum TTL (1–64) |
| `probes_per_hop` | 3 | Probe packets per TTL (1–5) |
| `probe_wait_seconds` | 0.5 | Maximum wait per individual reply |
| `trace_timeout_seconds` | 3 | Hard lifetime limit per trace command |
| `simultaneous_probes` | 32 | Concurrent packets inside one traceroute |
| `learning_samples` | 30 | Responding traces used to learn the initial sets |
| `confirmation_samples` | 3 | Required observations of a new address/TTL |
| `confirmation_window` | 10 | Recent traces examined for address/TTL additions |
| `multipath_grace_seconds` | 300 | Time before an unobserved learned branch can retire |
| `trace_retention_hours` | 24 | Raw observation retention |
| `event_retention_days` | 90 | Event and embedded evidence retention |

Slow traces overlap to preserve the one-second start cadence; per-destination
results are committed in start order even if commands finish out of order.
There are at most `ceil(trace_timeout_seconds / interval_seconds) + 2` pending
commands per destination. Overload skips slots instead of accumulating an
unbounded backlog; the dashboard reports missed slots since process start.
Scheduling is best effort under host load. A command exceeding its hard timeout
is killed/reaped and stored as an execution error with any partial hop output;
that partial path does not change the baseline. Increase timeouts for long or
slow paths. The HTTP health check returns 503 for stale results, command errors,
or a stopped collector; a valid trace with no destination reply is still a
healthy collector. Collection failures remain visible in the UI and logs.

At one-second cadence there are **86,400 traces/day/destination** and up to
90 packets/trace with the defaults. Start with a small target list, check router
ICMP policing and disk growth, and size storage for your actual hop count.
Retention pruning runs each minute in bounded 10,000-row batches. After lowering
retention or a long outage, clearing a large backlog takes multiple passes.
SQLite reuses freed pages; its file does not immediately shrink. Raw history is
finite; increase retention or export it if you need longer investigations.
Evidence windows are cleared after a collection gap longer than one confirmation
window, so old samples cannot confirm a newly observed branch after a restart.

## Reporting and operations

- `GET /api/status`: configured destinations, latest traces, learned state,
  freshness, missed slots, settings and collector health.
- `GET /api/events`: baseline, path-change and reachability events.
- `GET /api/traces`: raw parsed observations, including errors and timeouts.
- `GET /api/events.csv`: downloadable event report with JSON before/after paths,
  observed trace and confirmation evidence.
- `GET /api/openapi.json`: authenticated OpenAPI integration specification.
- `GET /healthz`: public boolean only; 200 while collection is fresh and
  error-free, otherwise 503.

History endpoints accept `target` (IP), `since`/`until` (inclusive Unix timestamps
in UTC seconds), `limit` (1–1,000, default 100), and `before_id` for pagination.
JSON responses contain `items` and `next_before_id`. Pass the cursor back with
the same filters until it is null. CSV exports one page; use `before_id` for
additional pages or narrow the time range. IDs are ordered by insertion; use
stored timestamps for temporal analysis across destinations.

Use the authenticated client example above with `/api/events.csv` to export
CSV, or `/api/traces` to retrieve raw history. Browser exports use the current
session automatically.

Data lives in the Compose `path-data` named volume. To back it up consistently,
stop the service, copy the **entire** volume including SQLite WAL/SHM files if
present, and start the service again. Restore into an empty volume while the
service is stopped. `docker compose down` retains data; `down -v` deletes it.

For a native Linux installation, install Python 3.10+ and the Linux `traceroute`
package, then run `python3 server.py --config /path/to/targets.json --database
/path/to/paths.sqlite3`. Provide raw ICMP permissions with the service manager;
use Docker if you do not already have a capability policy. macOS/BSD traceroute
flags differ and are not supported for real collection.

## Preview and checks

```bash
cd services/path-monitor
# After filling in .env, run a simulated preview using the same login.
# Stop any instance already using local port 8091 first.
docker compose -f compose.yaml run --build --rm --no-deps \
  -p 127.0.0.1:8091:8091 -e PATH_MONITOR_COOKIE_SECURE=false \
  path-monitor python server.py --demo --database /data/demo.sqlite3
# Open http://127.0.0.1:8091 and sign in — explicitly labeled simulated data.
python3 -m unittest discover -s tests -v
node --check static/app.js
node --test tests/test_ui.cjs
docker compose -f compose.yaml config --quiet
docker build -t netbox-path-monitor:test .
```

Demo mode needs no raw sockets, NetBox credentials or network access. Local
authentication is still required. It uses a temporary
database unless `--database` is explicitly supplied, learns normal alternating
branches, then simulates lost replies, recovery with an inserted hop, and a
return to the original path.
The Docker preview above explicitly uses a separate `demo.sqlite3` in the data
volume and leaves normal `paths.sqlite3` history untouched.
The tests exercise multipath, confirmation, missing replies, retirement, pinned
alternatives, shorter routes, restart persistence, ordered overlapping collection,
subprocess cleanup, retention and the real HTTP API.
