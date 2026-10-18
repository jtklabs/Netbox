"""Read-only HTTP dashboard and JSON history API for the path monitor."""

import argparse
import asyncio
import csv
import io
import json
import logging
import math
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from monitor import Collector, DEFAULTS, Observation, Store, load_config

STATIC = Path(__file__).parent / "static"
LOG = logging.getLogger("path-monitor")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, collector, demo=False):
        self.collector, self.demo = collector, demo
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def reply(self, body, content_type="application/json", status=200, attachment=None):
        if not isinstance(body, bytes):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            self.get()
        except (ValueError, OverflowError) as exc:
            self.reply(json.dumps({"error": str(exc)}), status=400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.exception("HTTP request failed")
            self.reply('{"error":"internal server error"}', status=500)

    def get(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path in {"/", "/app.js", "/style.css"}:
            name = "index.html" if path == "/" else path[1:]
            mime = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[name]
            return self.reply((STATIC / name).read_bytes(), mime)
        if path in {"/api/status", "/healthz"}:
            snapshot = self.server.collector.snapshot()
            snapshot["demo"] = self.server.demo
            return self.reply(json.dumps(snapshot), status=503 if path == "/healthz" and not snapshot["healthy"] else 200)
        if path not in {"/api/events", "/api/traces", "/api/events.csv"}:
            return self.reply('{"error":"not found"}', status=404)
        args = parse_qs(parsed.query, keep_blank_values=True)
        if set(args) - {"target", "since", "until", "limit", "before_id"} or any(len(v) != 1 for v in args.values()):
            raise ValueError("allowed query parameters: target, since, until, limit, before_id (one each)")
        target = args.get("target", [None])[0] or None
        if target is not None:
            import ipaddress
            target = str(ipaddress.ip_address(target))
        since = float(args.get("since", [0])[0])
        until = float(args.get("until", [1e20])[0])
        limit = int(args.get("limit", [100])[0])
        before_id = int(args["before_id"][0]) if "before_id" in args else None
        if not math.isfinite(since) or not math.isfinite(until) or since < 0 or until < since or not 1 <= limit <= 1000 or (before_id is not None and before_id < 1):
            raise ValueError("use valid Unix timestamps, a positive before_id, and limit 1..1000")
        table = "traces" if path == "/api/traces" else "events"
        rows = self.server.collector.store.rows(table, target, since, until, limit, before_id)
        cursor = rows[-1]["id"] if len(rows) == limit else None
        if path.endswith(".csv"):
            result = io.StringIO(newline="")
            writer = csv.writer(result)
            writer.writerow(["id", "target", "started_utc", "kind", "before", "after", "observed", "evidence_window"])
            for row in rows:
                writer.writerow([row["id"], row["target"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row["started"])), row["kind"],
                                 *[json.dumps(row.get(k)) for k in ("before", "after", "observed", "evidence_window")]])
            return self.reply(result.getvalue(), "text/csv; charset=utf-8", attachment="path-events.csv")
        self.reply(json.dumps({"items": rows, "next_before_id": cursor}))

    def log_message(self, fmt, *args):
        LOG.debug(fmt, *args)


def demo_config():
    return {"settings": DEFAULTS | {"learning_samples": 4, "confirmation_samples": 2, "multipath_grace_seconds": 8},
            "destinations": [{"address": "192.0.2.80", "name": "Example data center", "expected_hops": {}}]}


def demo_probe():
    count = 0

    async def run(address, settings):
        nonlocal count
        count += 1
        # A/B is normal multipath. C replaces it during the second half of a cycle.
        middle = ("198.51.100.10" if count % 2 else "198.51.100.11") if count % 40 < 20 else "203.0.113.20"
        return Observation(time.time(), 18, [
            {"ttl": ttl, "addresses": [ip], "rtt_ms": [float(ttl)], "timeouts": 0, "annotations": []}
            for ttl, ip in enumerate(["192.0.2.1", middle, address], 1)], True)
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("PATH_MONITOR_CONFIG", "targets.json"))
    parser.add_argument("--database", default=os.environ.get("PATH_MONITOR_DATABASE"))
    parser.add_argument("--host", default=os.environ.get("PATH_MONITOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PATH_MONITOR_PORT", "8091")))
    parser.add_argument("--demo", action="store_true", help="simulated probes; temporary database unless explicitly supplied")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    temporary = tempfile.TemporaryDirectory(prefix="path-monitor-") if args.demo and not args.database else None
    config = demo_config() if args.demo else load_config(args.config)
    db_path = args.database or (str(Path(temporary.name) / "demo.sqlite3") if temporary else "data/paths.sqlite3")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    collector = Collector(config, store, demo_probe()) if args.demo else Collector(config, store)
    server = Server((args.host, args.port), collector, args.demo)

    def collect():
        try:
            asyncio.run(collector.run())
        except Exception:
            LOG.exception("Collection stopped; health check will fail")

    worker = threading.Thread(target=collect, name="collector")
    worker.start()

    def stop(*_):
        collector.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info("Path monitor listening on %s:%s%s", args.host, args.port, " (SIMULATED DATA)" if args.demo else "")
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        collector.stop.set()
        worker.join()
        server.server_close()
        store.close()
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    main()
