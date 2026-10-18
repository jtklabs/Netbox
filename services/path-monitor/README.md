# Path monitor

A standalone service for continuously tracing a configured list of IPv4/IPv6
destinations and reporting route changes. It lives alongside NetBox but has no
dependency on NetBox, its database, or its API. Deploy one instance at each
network vantage point you want to measure.

- Starts an ICMP traceroute to **each destination every second** by default.
- Records every finished trace, including per-TTL address sets, RTTs, missing
  replies, ICMP annotations, execution errors, start time and elapsed time.
- Keeps accepted multipath address sets per hop, rather than treating every
  alternating response as a route change.
- Stores timestamped baseline, path-change and reachability events with the
  before/after paths, confirming trace, and recent confirmation evidence.
- Provides an automatically updating dashboard, destination/time filters,
  expandable comparisons, raw trace history, paginated JSON and CSV export.
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
docker compose -f compose.yaml up -d --build
```

Open `http://127.0.0.1:8091` on that server. For direct LAN access:

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

The service uses Linux `traceroute -I -n` with only the `NET_RAW` capability;
it does not need privileged mode, host PID access, or NetBox credentials.
The container runs as root with all other capabilities removed so it can open
ICMP raw sockets and write the named data volume. The filesystem is read-only
apart from that volume. The web interface is **read-only and has no login**.
Use an authenticated internal reverse proxy or existing internal access
controls. Route information is visible to anyone who can reach the service.
Proxy to port 8091 at a hostname root, or strip a subpath prefix and retain its
trailing slash. There are no external scripts, fonts or CDNs.

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
    {"name": "IPv6 endpoint", "address": "2001:db8::80"}
  ]
}
```

Replace the example addresses before deploying. Addresses must be numeric IP
literals; hostnames, multicast, unspecified and scoped IPv6 addresses are
rejected. Up to 64 unique destinations can be configured. Names are optional.
`expected_hops` is optional: it maps hop numbers (TTL) to known alternatives.
These addresses stay accepted even when rarely observed. Other addresses at
that TTL are still monitored and reported.

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
   path-change event records the removal. Pinned `expected_hops` never expire.
   A switch from `{A,B}` to `C` therefore first records `{A,B,C}`, then `{C}`
   once the old branches age out. The exact latest observation is always stored
   separately from the accepted sets.
4. **Ignore unknowns:** `*`, a silent hop, a command failure, and partial output
   from a killed trace cannot delete an accepted hop. A shorter route removes
   trailing hops only after repeated replies from the destination at its new
   TTL. Destination-TTL changes use the same 3-of-10 confirmation rule.
5. **Track replies separately:** 3 consecutive completed traces with the same
   destination reply status confirm reached/no-reply transitions. This is an
   observation of ICMP replies, not a claim that an application is down. Command
   failures break the confirmation sequence and have no topology effect.

Ordinary A/B switching creates no path events once learned. A branch that first
appears later produces one addition event after confirmation, then becomes
normal too. Increase the grace period or pin expected alternatives for branches
that can be absent for more than five minutes. Silent TTLs retain their last
known sets; absence of evidence does not establish removal. The detector compares
sets at each TTL, not correlations between complete paths; accepted sets do not
prove every possible combination of addresses is a real end-to-end path.

This is repeated ICMP traceroute, not a Paris traceroute or exhaustive ECMP
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
- `GET /healthz`: 200 while collection is fresh and error-free, otherwise 503.

History endpoints accept `target` (IP), `since`/`until` (inclusive Unix timestamps
in UTC seconds), `limit` (1–1,000, default 100), and `before_id` for pagination.
JSON responses contain `items` and `next_before_id`. Pass the cursor back with
the same filters until it is null. CSV exports one page; use `before_id` for
additional pages or narrow the time range. IDs are ordered by insertion; use
stored timestamps for temporal analysis across destinations.

```bash
curl 'http://127.0.0.1:8091/api/events?target=10.20.30.40&limit=100'
curl 'http://127.0.0.1:8091/api/events.csv?since=1792281600&limit=1000' -o changes.csv
```

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
python3 server.py --demo
# Open http://127.0.0.1:8091 — explicitly labeled simulated data.
python3 -m unittest discover -s tests -v
node --check static/app.js
docker compose -f compose.yaml config --quiet
docker build -t netbox-path-monitor:test .
```

Demo mode needs no raw sockets, credentials or network access. It uses a temporary
database unless `--database` is explicitly supplied, learns normal alternating
branches, then periodically introduces a different branch to show change events.
The tests exercise multipath, confirmation, missing replies, retirement, pinned
alternatives, shorter routes, restart persistence, ordered overlapping collection,
subprocess cleanup, retention and the real HTTP API.
