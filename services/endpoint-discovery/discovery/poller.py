"""Outbound-only poller with a durable SQLite outbox and cached assignments."""
import argparse
import json
import logging
import os
import sqlite3
import ssl
import time
from pathlib import Path

import httpx

from .collector import collect
from .poller_url import secure_url
from .schemas import Target

log = logging.getLogger("endpoint-poller")


class Outbox:
    def __init__(self, path, max_bytes=512 * 1024 * 1024):
        directory = Path(path)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        self.connection = sqlite3.connect(directory / "outbox.sqlite3")
        os.chmod(directory / "outbox.sqlite3", 0o600)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY, payload TEXT NOT NULL, rejected INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS assignments (
                id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, fetched_at INTEGER NOT NULL
            );
        """)
        self.max_bytes = max_bytes

    def check_capacity(self, extra=0):
        used = self.connection.execute("SELECT COALESCE(SUM(length(payload)),0) FROM reports").fetchone()[0]
        if used + extra > self.max_bytes:
            raise RuntimeError("outbox is full; restore delivery or archive rejected reports")

    def put(self, report):
        payload = report.model_dump_json()
        self.check_capacity(len(payload.encode()))
        with self.connection:
            self.connection.execute("INSERT INTO reports(payload) VALUES (?)", (payload,))

    def save_targets(self, targets):
        payload = json.dumps([t.model_dump() for t in targets])
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO assignments VALUES(1,?,?)", (payload, int(time.time())))

    def cached_targets(self, now=None):
        row = self.connection.execute("SELECT payload,fetched_at FROM assignments WHERE id=1").fetchone()
        if not row or (int(time.time()) if now is None else now) - row[1] > 7 * 86400:
            raise RuntimeError("no recent cached assignments; cannot collect")
        return [Target.model_validate(t) for t in json.loads(row[0])]

    def flush(self, client):
        cursor = 0
        while True:
            row = self.connection.execute(
                "SELECT id,payload FROM reports WHERE rejected=0 AND id>? ORDER BY id LIMIT 1", (cursor,)).fetchone()
            if row is None:
                return True
            row_id, payload = row
            cursor = row_id
            try:
                response = client.post("v1/reports", content=payload, headers={"Content-Type": "application/json"})
            except httpx.HTTPError:
                return False
            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError:
                    return False
                if not isinstance(body, dict) or body.get("accepted") is not True:
                    return False
                with self.connection:
                    self.connection.execute("DELETE FROM reports WHERE id=?", (row_id,))
            elif response.status_code in (403, 409, 413, 422):
                # Preserve rejected evidence for operator inspection. Continue
                # delivery for other targets instead of wedging the whole site.
                with self.connection:
                    self.connection.execute("UPDATE reports SET rejected=1 WHERE id=?", (row_id,))
                log.error("report %d quarantined (HTTP %d)", row_id, response.status_code)
            else:
                return False


def cycle(client, outbox, credentials):
    delivered = outbox.flush(client)
    try:
        response = client.get("v1/targets")
        response.raise_for_status()
        targets = [Target.model_validate(t) for t in response.json()["items"]]
        outbox.save_targets(targets)
    except (httpx.HTTPError, ValueError, KeyError):
        # An explicit authorization failure revokes cached assignments.
        if "response" in locals() and response.status_code in (401, 403):
            raise RuntimeError("poller authorization rejected; cached assignments will not be used") from None
        targets = outbox.cached_targets()
        delivered = False
        log.warning("using cached target assignments")
    failures = 0
    for target in targets:
        for context in target.contexts:
            outbox.check_capacity()
            profile = credentials.get(target.credential)
            if profile is None:
                log.error("credential profile missing for target %s", target.key)
                failures += 1
                continue
            report = collect(target, context, profile)
            outbox.put(report)
            failures += sum(table.status == "error" for table in report.tables)
            delivered = outbox.flush(client) and delivered
    rejected = outbox.connection.execute("SELECT COUNT(*) FROM reports WHERE rejected=1").fetchone()[0]
    log.info("cycle completed: %d targets, %d collection errors, %d quarantined reports", len(targets), failures, rejected)
    return delivered and failures == 0 and rejected == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    url = secure_url(os.environ["DISCOVERY_URL"])
    token = Path(os.environ["DISCOVERY_TOKEN_FILE"]).read_text().strip()
    if len(token) < 32:
        raise ValueError("a poller token of at least 32 characters is required")
    credentials = json.loads(Path(os.environ["DISCOVERY_CREDENTIALS_FILE"]).read_text())
    # Reject multiline config directives before anything reaches snmp.conf.
    allowed = {"security_name", "auth_protocol", "auth_passphrase", "priv_protocol", "priv_passphrase"}
    for profile in credentials.values():
        if set(profile) - allowed or any(not isinstance(v, str) or "\n" in v or "\r" in v for v in profile.values()):
            raise ValueError("invalid SNMP credential profile")
        if not all(profile.get(k) for k in ("security_name", "auth_passphrase", "priv_passphrase")):
            raise ValueError("SNMPv3 authPriv credentials are required")
    outbox = Outbox(os.getenv("DISCOVERY_SPOOL_DIR", "/var/lib/endpoint-discovery"))
    interval = int(os.getenv("DISCOVERY_POLL_SECONDS", "300"))
    if interval < 30:
        raise ValueError("poll interval must be at least 30 seconds")
    trust = ssl.create_default_context(cafile=os.getenv("DISCOVERY_CA_FILE"))
    with httpx.Client(base_url=url + "/", headers={"Authorization": "Bearer " + token},
                      verify=trust, follow_redirects=False, timeout=60) as client:
        while True:
            try:
                healthy = cycle(client, outbox, credentials)
            except (RuntimeError, httpx.HTTPError):
                log.error("cycle failed; inspect connectivity, authorization, and outbox capacity")
                healthy = False
            if args.once:
                return 0 if healthy else 1
            time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
