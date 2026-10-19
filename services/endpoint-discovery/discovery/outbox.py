"""Durable outbox; one connection per owner thread, with leased uploads."""
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import httpx

from .schemas import Target

log = logging.getLogger("endpoint-poller")


class Outbox:
    def __init__(self, path, max_bytes=512 * 1024 * 1024):
        directory = Path(path)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        self.connection = sqlite3.connect(directory / "outbox.sqlite3", timeout=30)
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
        # Serialize migration across the coordinator and uploader connections.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if "lease_until" not in {r[1] for r in self.connection.execute("PRAGMA table_info(reports)")}:
                self.connection.execute("ALTER TABLE reports ADD COLUMN lease_until INTEGER NOT NULL DEFAULT 0")
            if "stream_key" not in {r[1] for r in self.connection.execute("PRAGMA table_info(reports)")}:
                self.connection.execute("ALTER TABLE reports ADD COLUMN stream_key TEXT NOT NULL DEFAULT ''")
                self.connection.execute(
                    "UPDATE reports SET stream_key=CASE WHEN json_valid(payload) "
                    "THEN COALESCE(json_extract(payload,'$.device_key'),'legacy-'||id) ELSE 'legacy-'||id END")
            self.connection.execute("CREATE INDEX IF NOT EXISTS reports_stream ON reports(stream_key,rejected,id)")
            self.connection.execute("CREATE INDEX IF NOT EXISTS reports_pending ON reports(rejected,id)")
        self.max_bytes = max_bytes
        self.last_flush_attempted = False

    def check_capacity(self, extra=0):
        used = self.connection.execute("SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM reports").fetchone()[0]
        if used + extra > self.max_bytes:
            raise RuntimeError("outbox is full; restore delivery or archive rejected reports")

    def put(self, report):
        payload = report.model_dump_json()
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.check_capacity(len(payload.encode()))
            self.connection.execute("INSERT INTO reports(payload,stream_key) VALUES (?,?)", (payload, report.device_key))

    def save_targets(self, targets):
        payload = json.dumps([t.model_dump() for t in targets])
        with self.connection:
            self.connection.execute("INSERT OR REPLACE INTO assignments VALUES(1,?,?)", (payload, int(time.time())))

    def cached_targets(self, now=None):
        row = self.connection.execute("SELECT payload,fetched_at FROM assignments WHERE id=1").fetchone()
        if not row or (int(time.time()) if now is None else now) - row[1] > 7 * 86400:
            raise RuntimeError("no recent cached assignments; cannot collect")
        return [Target.model_validate(t) for t in json.loads(row[0])]

    def assignment_age(self):
        row = self.connection.execute("SELECT fetched_at FROM assignments WHERE id=1").fetchone()
        return time.time() - row[0] if row else float("inf")

    def stats(self):
        pending, rejected, size = self.connection.execute(
            "SELECT COALESCE(SUM(rejected=0),0),COALESCE(SUM(rejected=1),0),"
            "COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM reports").fetchone()
        return {"pending_reports": pending, "quarantined_reports": rejected, "spool_bytes": size}

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT r.id,r.payload FROM reports r WHERE r.rejected=0 AND r.lease_until<=? "
                "AND NOT EXISTS (SELECT 1 FROM reports older WHERE older.stream_key=r.stream_key "
                "AND older.rejected=0 AND older.id<r.id) ORDER BY r.id LIMIT 1", (now,)
            ).fetchone()
            if row:
                self.connection.execute("UPDATE reports SET lease_until=? WHERE id=?", (now + 120, row[0]))
            return row

    def flush(self, client, limit=None, stop=None, on_unauthorized=None, retry_delay=0):
        self.last_flush_attempted = False
        count = 0
        while True:
            if (limit is not None and count >= limit) or (stop is not None and stop.is_set()):
                return True
            row = self.claim()
            if row is None:
                return True
            row_id, payload = row
            self.last_flush_attempted = True
            try:
                response = client.post("v1/reports", content=payload, headers={"Content-Type": "application/json"})
            except httpx.HTTPError:
                self.retry(row_id, payload, retry_delay)
                return False
            if response.status_code == 200:
                try:
                    body = response.json()
                except ValueError:
                    self.retry(row_id, payload, retry_delay)
                    return False
                if not isinstance(body, dict) or body.get("accepted") is not True:
                    self.retry(row_id, payload, retry_delay)
                    return False
                with self.connection:
                    self.connection.execute("DELETE FROM reports WHERE id=? AND payload=?", (row_id, payload))
            elif response.status_code in (403, 409, 413, 422):
                # Preserve rejected evidence for operator inspection. Continue
                # delivery for other targets instead of wedging the whole site.
                with self.connection:
                    self.connection.execute("UPDATE reports SET rejected=1 WHERE id=? AND payload=?", (row_id, payload))
                log.error("report %d quarantined (HTTP %d)", row_id, response.status_code)
            else:
                self.retry(row_id, payload, retry_delay)
                if response.status_code == 401 and on_unauthorized:
                    on_unauthorized()
                return False
            count += 1

    def retry(self, row_id, payload, delay):
        # SQLite may reuse IDs after deletion. Late replies from an expired
        # lease must never acknowledge or change a different report at that ID.
        with self.connection:
            self.connection.execute("UPDATE reports SET lease_until=? WHERE id=? AND payload=?",
                                    (time.time() + delay, row_id, payload))
