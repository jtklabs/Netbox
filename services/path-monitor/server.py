"""Read-only HTTP dashboard and JSON history API for the path monitor."""

import argparse
import asyncio
import csv
import hmac
import html
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
from auth import Auth, SESSION_SECONDS
from api import openapi

STATIC = Path(__file__).parent / "static"
LOG = logging.getLogger("path-monitor")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, collector, auth, demo=False):
        self.collector, self.auth, self.demo = collector, auth, demo
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, body, content_type="application/json", status=200, attachment=None, headers=()):
        if not isinstance(body, bytes):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        # no-referrer turns native form POST origins into null in browsers.
        # Keep same-origin login/logout working without sending referrers off-site.
        self.send_header("Referrer-Policy", "same-origin")
        for name, value in headers:
            self.send_header(name, value)
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.dispatch(self.get)

    def do_POST(self):
        self.dispatch(self.post)

    def dispatch(self, handler):
        try:
            handler()
        except (ValueError, OverflowError) as exc:
            self.reply(json.dumps({"error": str(exc)}), status=400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            LOG.exception("HTTP request failed")
            self.reply('{"error":"internal server error"}', status=500)

    def login_page(self, error="", status=200):
        auth = self.server.auth
        challenge = auth.cookies(self.headers.get("Cookie")).get(auth.login_cookie, "")
        if auth.valid_challenge(challenge, self.headers.get("Cookie")):
            cookie = auth.cookie(auth.login_cookie, challenge, 600)
        else:
            challenge, cookie = auth.login_challenge()
        page = (STATIC / "login.html").read_text().replace("{{CSRF}}", html.escape(challenge, quote=True)).replace("{{ERROR}}", html.escape(error))
        headers = [("Set-Cookie", cookie)]
        if status == 429:
            headers.append(("Retry-After", "600"))
        self.reply(page, "text/html; charset=utf-8", status, headers=headers)

    def form(self):
        if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/x-www-form-urlencoded":
            raise ValueError("use an encoded HTML form")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 16384:
            raise ValueError("invalid form size")
        values = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True, max_num_fields=4)
        if any(len(v) != 1 for v in values.values()):
            raise ValueError("duplicate form fields")
        return {k: v[0] for k, v in values.items()}

    def valid_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True  # Signed login challenge / session CSRF token is still required.
        parsed = urlsplit(origin)
        return (parsed.scheme == ("https" if self.server.auth.secure else "http")
                and parsed.netloc.lower() == self.headers.get("Host", "").lower()
                and not parsed.path and not parsed.query and not parsed.fragment)

    def post(self):
        path = urlsplit(self.path).path
        auth = self.server.auth
        if path not in {"/login", "/logout"}:
            return self.reply('{"error":"not found"}', status=404)
        form = self.form()
        if not self.valid_origin():
            return self.reply('{"error":"invalid request origin"}', status=403)
        if path == "/login":
            if not auth.valid_challenge(form.get("csrf", ""), self.headers.get("Cookie")):
                return self.login_page("Sign-in form expired. Please try again.", 403)
            username, password = form.get("username", ""), form.get("password", "")
            if len(username) > 100 or not 1 <= len(password) <= 1024:
                return self.login_page("Invalid username or password.", 401)
            token, status = auth.login(username, password, self.client_address[0])
            if not token:
                return self.login_page("Too many sign-in attempts. Try again later." if status == 429 else "Invalid username or password.", status)
            # Rotate on every login and revoke any previous session in this browser.
            auth.logout(self.headers.get("Cookie"))
            return self.reply("", status=303, headers=[("Location", "."),
                ("Set-Cookie", auth.cookie(auth.session_cookie, token, SESSION_SECONDS)),
                ("Set-Cookie", auth.cookie(auth.login_cookie, "", 0))])
        session = auth.session(self.headers.get("Cookie"))
        if not session or not hmac.compare_digest(form.get("csrf", "").encode(), session["csrf"].encode()):
            return self.reply('{"error":"invalid session or CSRF token"}', status=403)
        auth.logout(self.headers.get("Cookie"))
        self.reply("", status=303, headers=[("Location", "login"), ("Set-Cookie", auth.cookie(auth.session_cookie, "", 0))])

    def get(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/healthz":
            healthy = self.server.collector.snapshot()["healthy"]
            return self.reply(json.dumps({"healthy": healthy}), status=200 if healthy else 503)
        if path == "/login":
            return self.login_page()
        if path == "/style.css":
            return self.reply((STATIC / "style.css").read_bytes(), "text/css; charset=utf-8")
        if path not in {"/", "/app.js", "/api/status", "/api/events", "/api/traces", "/api/events.csv", "/api/openapi.json"}:
            return self.reply('{"error":"not found"}', status=404)
        auth = self.server.auth
        authorization = self.headers.get("Authorization")
        session = auth.session(self.headers.get("Cookie")) if not authorization else None
        key = auth.api_key(authorization) if path.startswith("/api/") else None
        if not session and not key:
            if path.startswith("/api/"):
                return self.reply('{"error":"authentication required"}', status=401,
                                  headers=[("WWW-Authenticate", 'Bearer realm="path-monitor"')])
            return self.reply("", status=303, headers=[("Location", "login")])
        if path == "/api/openapi.json":
            return self.reply(json.dumps(openapi()))
        if path in {"/", "/app.js", "/style.css"}:
            name = "index.html" if path == "/" else path[1:]
            mime = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[name]
            body = (STATIC / name).read_text()
            if path == "/":
                body = body.replace("{{CSRF}}", html.escape(session["csrf"], quote=True)).replace("{{USERNAME}}", html.escape(session["username"]))
            return self.reply(body, mime)
        if path == "/api/status":
            snapshot = self.server.collector.snapshot()
            snapshot["demo"] = self.server.demo
            return self.reply(json.dumps(snapshot))
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
            fields = ("before", "after", "observed", "evidence_window", "hop_moves",
                      "first_observed_at", "last_reached_observation", "last_responding_hop", "accepted_path", "no_reply_since")
            writer.writerow(["id", "target", "started_utc", "kind", *fields])
            for row in rows:
                writer.writerow([row["id"], row["target"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row["started"])), row["kind"],
                                 *[json.dumps(row.get(k)) for k in fields]])
            return self.reply(result.getvalue(), "text/csv; charset=utf-8", attachment="path-events.csv")
        self.reply(json.dumps({"items": rows, "next_before_id": cursor}))

    def log_request(self, code="-", size="-"):
        # Do not log credentials, cookies, Authorization headers or query strings.
        LOG.debug("%s %s %s", self.command, urlsplit(self.path).path, code)

    def log_message(self, fmt, *args):
        pass


def demo_config():
    return {"settings": DEFAULTS | {"learning_samples": 4, "confirmation_samples": 2, "multipath_grace_seconds": 8},
            "destinations": [{"address": "192.0.2.80", "name": "Example data center", "expected_hops": {}}]}


def demo_probe():
    count = 0

    async def run(address, settings):
        nonlocal count
        count += 1
        # Normal multipath, lost replies, an inserted hop, then the original path.
        middle = "198.51.100.10" if count % 2 else "198.51.100.11"
        phase = count % 60
        reached = not 20 <= phase < 30
        addresses = (["192.0.2.1", None, None] if not reached else
                     ["192.0.2.1", "203.0.113.20", middle, address] if 30 <= phase < 50 else
                     ["192.0.2.1", middle, address])
        output = f"traceroute to {address} ({address}), {settings['max_hops']} hops max, 60 byte packets\n"
        output += "".join(f" {ttl:2}  {ip}  {ttl:.3f} ms\n" if ip else f" {ttl:2}  * * *\n"
                          for ttl, ip in enumerate(addresses, 1))
        return Observation(time.time(), 18, [
            {"ttl": ttl, "addresses": [ip] if ip else [], "rtt_ms": [float(ttl)] if ip else [],
             "timeouts": 0 if ip else 3, "annotations": []}
            for ttl, ip in enumerate(addresses, 1)], reached, raw_output=output)
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("PATH_MONITOR_CONFIG", "targets.json"))
    parser.add_argument("--database", default=os.environ.get("PATH_MONITOR_DATABASE"))
    parser.add_argument("--host", default=os.environ.get("PATH_MONITOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PATH_MONITOR_PORT", "8091")))
    parser.add_argument("--demo", action="store_true", help="simulated probes; temporary database unless explicitly supplied")
    args = parser.parse_args()
    try:
        auth = Auth.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    temporary = tempfile.TemporaryDirectory(prefix="path-monitor-") if args.demo and not args.database else None
    config = demo_config() if args.demo else load_config(args.config)
    db_path = args.database or (str(Path(temporary.name) / "demo.sqlite3") if temporary else "data/paths.sqlite3")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    collector = Collector(config, store, demo_probe()) if args.demo else Collector(config, store)
    server = Server((args.host, args.port), collector, auth, args.demo)

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
