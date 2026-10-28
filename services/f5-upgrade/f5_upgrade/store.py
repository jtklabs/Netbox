"""SQLite queue. No automatic replay of ambiguous device operations."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


class Conflict(Exception):
    pass


class LostOwnership(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, device TEXT NOT NULL, lock_key TEXT NOT NULL,
                    kind TEXT NOT NULL, parent TEXT, request TEXT NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                    worker TEXT, heartbeat REAL, result TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS locks (key TEXT PRIMARY KEY, job TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL,
                    time REAL NOT NULL, step TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snmp_rows (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT NOT NULL,
                    root_oid TEXT NOT NULL, oid TEXT NOT NULL, type TEXT NOT NULL,
                    value TEXT NOT NULL, encoding TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS snmp_rows_job ON snmp_rows(job, seq);
                CREATE INDEX IF NOT EXISTS events_job ON events(job, seq);
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, created);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            raise KeyError("job not found")
        item = dict(row)
        item["lock_held"] = bool(item.get("lock_held", False))
        for field in ("request", "result"):
            item[field] = json.loads(item[field])
        return item

    def get(self, job_id):
        with self.connect() as db:
            return self.decode(
                db.execute(
                    "SELECT jobs.*, EXISTS(SELECT 1 FROM locks WHERE job=jobs.id) AS lock_held FROM jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
            )

    def list(self, limit=100, offset=0):
        with self.connect() as db:
            return [
                self.decode(r)
                for r in db.execute(
                    "SELECT jobs.*, EXISTS(SELECT 1 FROM locks WHERE job=jobs.id) AS lock_held FROM jobs ORDER BY created DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def submit(self, request, lock_key, key=None, kind="upgrade", parent=None):
        payload = json.dumps(request, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                existing = db.execute(
                    "SELECT jobs.*, EXISTS(SELECT 1 FROM locks WHERE job=jobs.id) AS lock_held FROM jobs WHERE idempotency_key=?",
                    (key,),
                ).fetchone()
                if existing:
                    if (
                        existing["request"] != payload
                        or existing["kind"] != kind
                        or existing["parent"] != parent
                    ):
                        raise Conflict("idempotency key was used with different parameters")
                    return self.decode(existing)
            if parent:
                source = self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (parent,)).fetchone())
                if source["status"] not in ("succeeded", "failed", "resolved"):
                    raise Conflict("finish or resolve the source job before requesting rollback")
                if "rollback" not in source["result"]:
                    raise Conflict("source job has no recorded rollback target")
            now, job_id = time.time(), str(uuid.uuid4())
            db.execute(
                """INSERT INTO jobs
                (id,device,lock_key,kind,parent,request,status,created,updated,idempotency_key)
                VALUES (?,?,?,?,?,?,'queued',?,?,?)""",
                (
                    job_id,
                    request.get("device", request.get("device_ip")),
                    lock_key,
                    kind,
                    parent,
                    payload,
                    now,
                    now,
                    key,
                ),
            )
        return self.get(job_id)

    def claim(self, worker):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT * FROM jobs WHERE status='queued' AND (
                lock_key NOT IN (SELECT key FROM locks) OR (
                    kind='rollback' AND EXISTS (
                        SELECT 1 FROM locks JOIN jobs parent ON parent.id=locks.job
                        WHERE locks.key=jobs.lock_key AND locks.job=jobs.parent AND parent.status='failed'
                    )
                )) ORDER BY created LIMIT 1""").fetchone()
            if not row:
                return None
            inherited = db.execute("DELETE FROM locks WHERE key=?", (row["lock_key"],)).rowcount
            db.execute("INSERT INTO locks VALUES (?,?)", (row["lock_key"], row["id"]))
            if inherited:
                db.execute(
                    "UPDATE jobs SET result=? WHERE id=?", (json.dumps({"inherited_lock": True}), row["id"])
                )
            db.execute(
                "UPDATE jobs SET status='running',worker=?,heartbeat=?,updated=? WHERE id=?",
                (worker, time.time(), time.time(), row["id"]),
            )
        return self.get(row["id"])

    def guard(self, job, worker):
        with self.connect() as db:
            row = db.execute("SELECT status,worker FROM jobs WHERE id=?", (job,)).fetchone()
            if not row or row["status"] != "running" or row["worker"] != worker:
                raise LostOwnership("job ownership was revoked; device requires inspection")

    def heartbeat(self, job, worker):
        with self.connect() as db:
            return (
                db.execute(
                    "UPDATE jobs SET heartbeat=? WHERE id=? AND worker=? AND status='running'",
                    (time.time(), job, worker),
                ).rowcount
                == 1
            )

    def reap(self, seconds):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            stale = db.execute(
                "SELECT id,result FROM jobs WHERE kind='snmp_walk' AND status='running' AND heartbeat<?",
                (time.time() - seconds,),
            ).fetchall()
            for row in stale:
                result = json.loads(row["result"])
                result["error"] = "SNMP worker heartbeat expired; partial results retained"
                for item in result.get("oids", []):
                    if item["status"] in ("pending", "running"):
                        item.update(status="failed", error="worker heartbeat expired")
                db.execute(
                    "UPDATE jobs SET status='failed',result=?,updated=? WHERE id=?",
                    (json.dumps(result), time.time(), row["id"]),
                )
                db.execute("DELETE FROM locks WHERE job=?", (row["id"],))
            # Locks deliberately survive stale SSH workers: do not run a second job on an ambiguous device.
            db.execute(
                """UPDATE jobs SET status='recovery_required',updated=?
                WHERE status='running' AND kind!='snmp_walk' AND heartbeat<?""",
                (time.time(), time.time() - seconds),
            )

    def record(self, job, worker, step, data=None, result=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            if row["status"] != "running" or row["worker"] != worker:
                raise LostOwnership("job is no longer owned")
            db.execute(
                "INSERT INTO events(job,time,step,data) VALUES (?,?,?,?)",
                (job, time.time(), step, json.dumps(data or {})),
            )
            if result is not None:
                db.execute(
                    "UPDATE jobs SET result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), job)
                )

    def finish(self, job, worker, status, retain_lock=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE jobs SET status=?,updated=? WHERE id=? AND worker=? AND status='running'",
                (status, time.time(), job, worker),
            ).rowcount
            if changed and status != "recovery_required" and not retain_lock:
                db.execute("DELETE FROM locks WHERE job=?", (job,))

    def events(self, job, after=0, limit=500):
        self.get(job)
        with self.connect() as db:
            return [
                {**dict(r), "data": json.loads(r["data"])}
                for r in db.execute(
                    "SELECT * FROM events WHERE job=? AND seq>? ORDER BY seq LIMIT ?", (job, after, limit)
                )
            ]

    def cancel(self, job):
        with self.connect() as db:
            if not db.execute(
                "UPDATE jobs SET status='cancelled',updated=? WHERE id=? AND status='queued'",
                (time.time(), job),
            ).rowcount:
                raise Conflict("only queued jobs can be cancelled")
        return self.get(job)

    def resolve(self, job, resolution):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()
            held = db.execute("SELECT 1 FROM locks WHERE job=?", (job,)).fetchone()
            if not row or row["status"] not in ("recovery_required", "failed") or not held:
                raise Conflict("only failed or recovery_required jobs holding a lock can be resolved")
            db.execute("UPDATE jobs SET status='resolved',updated=? WHERE id=?", (time.time(), job))
            db.execute("DELETE FROM locks WHERE job=?", (job,))
            db.execute(
                "INSERT INTO events(job,time,step,data) VALUES (?,?,?,?)",
                (job, time.time(), "operator_resolution", json.dumps(resolution)),
            )
        return self.get(job)

    def append_snmp_rows(self, job, worker, rows, result):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owned = db.execute(
                "SELECT 1 FROM jobs WHERE id=? AND worker=? AND status='running' AND kind='snmp_walk'",
                (job, worker),
            ).fetchone()
            if not owned:
                raise LostOwnership("job is no longer owned")
            db.executemany(
                "INSERT INTO snmp_rows(job,root_oid,oid,type,value,encoding) VALUES (?,?,?,?,?,?)",
                [(job, r["root_oid"], r["oid"], r["type"], r["value"], r["encoding"]) for r in rows],
            )
            db.execute(
                "UPDATE jobs SET result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), job)
            )

    def snmp_results(self, job, after=0, limit=500):
        # Read status and rows from one snapshot: terminal=true must include all committed rows.
        with self.connect() as db:
            db.execute("BEGIN")
            state = db.execute("SELECT kind,status FROM jobs WHERE id=?", (job,)).fetchone()
            if state is None or state["kind"] != "snmp_walk":
                raise KeyError("SNMP job not found")
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT seq,root_oid,oid,type,value,encoding FROM snmp_rows WHERE job=? AND seq>? ORDER BY seq LIMIT ?",
                    (job, after, limit + 1),
                )
            ]
            more = len(rows) > limit
            rows = rows[:limit]
            return {
                "id": job,
                "status": state["status"],
                "finished": state["status"] not in ("queued", "running"),
                "rows": rows,
                "next_cursor": rows[-1]["seq"] if rows else after,
                "has_more": more,
            }
