"""Collection scheduling, target refresh, and HTTPS delivery run independently."""
import json
import logging
import os
import signal
import sqlite3
import threading
import time
from pathlib import Path

import httpx

from .outbox import Outbox
from .scheduler import Scheduler
from .schemas import Target

log = logging.getLogger("endpoint-poller")


class Assignments:
    def __init__(self):
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.targets = []
        self.authorized = False
        self.revoked = False
        self.expires = 0
        self.delivery_healthy = True

    def revoke(self):
        with self.lock:
            self.authorized, self.revoked = False, True

    def snapshot(self):
        with self.lock:
            return list(self.targets), self.authorized and time.time() < self.expires


class Runtime:
    def __init__(self, path, credentials, client_factory, workers=32, interval=60,
                 upload_workers=2, refresh_seconds=60, device_timeout=120, once=False):
        if not 1 <= workers <= 256 or not 1 <= upload_workers <= 16 or interval < 30 or refresh_seconds < 5:
            raise ValueError("invalid poller concurrency or scheduling settings")
        if not 1 <= device_timeout <= 600:
            raise ValueError("device timeout must be 1–600 seconds")
        self.path, self.client_factory = Path(path), client_factory
        self.scheduler = Scheduler(credentials, workers=workers, interval=interval, device_timeout=device_timeout,
                                   stagger=not once, repeat=not once)
        self.upload_workers, self.refresh_seconds, self.once = upload_workers, refresh_seconds, once
        self.assignments = Assignments()
        self.stop = self.scheduler.stop
        self.worker_failed = threading.Event()
        self.threads = []

    def _safe_worker(self, target):
        try:
            target()
        except Exception as exc:
            log.error("background I/O worker failed (%s); restarting poller", type(exc).__name__)
            self.worker_failed.set()
            self.stop.set()

    def _refresh(self):
        outbox = Outbox(self.path)
        try:
            with self.client_factory() as client:
                while not self.stop.is_set():
                    try:
                        response = client.get("v1/targets")
                        if response.status_code in (401, 403):
                            self.assignments.revoke()
                            raise RuntimeError("authorization rejected")
                        response.raise_for_status()
                        targets = [Target.model_validate(t) for t in response.json()["items"]]
                        outbox.save_targets(targets)
                        with self.assignments.lock:
                            self.assignments.targets = targets
                            self.assignments.expires = time.time() + 7 * 86400
                            self.assignments.authorized, self.assignments.revoked = True, False
                    except (RuntimeError, httpx.HTTPError, ValueError, KeyError, sqlite3.Error):
                        log.warning("target refresh failed; using unexpired assignments only if authorization remains valid")
                        try:
                            targets = outbox.cached_targets()
                            with self.assignments.lock:
                                self.assignments.targets = targets
                                self.assignments.expires = time.time() + max(0, 7 * 86400 - outbox.assignment_age())
                                self.assignments.authorized = not self.assignments.revoked
                        except (RuntimeError, ValueError, sqlite3.Error):
                            with self.assignments.lock:
                                self.assignments.authorized = False
                    finally:
                        self.assignments.ready.set()
                    if self.once:
                        return
                    self.stop.wait(self.refresh_seconds)
        finally:
            outbox.connection.close()

    def _upload(self):
        outbox = Outbox(self.path)
        delay = 1
        try:
            with self.client_factory() as client:
                while not self.stop.is_set():
                    with self.assignments.lock:
                        revoked = self.assignments.revoked
                    if revoked:
                        self.stop.wait(1)
                        continue
                    try:
                        healthy = outbox.flush(client, limit=16, stop=self.stop,
                                               on_unauthorized=self.assignments.revoke, retry_delay=delay)
                    except (sqlite3.Error, httpx.HTTPError):
                        healthy = False
                    if outbox.last_flush_attempted:
                        with self.assignments.lock:
                            self.assignments.delivery_healthy = healthy
                    delay = 1 if healthy else min(delay * 2, 30)
                    if not healthy:
                        log.warning("report delivery unavailable; collection continues into durable outbox")
                    self.stop.wait(0.2 if healthy else delay)
        finally:
            outbox.connection.close()

    def status(self, outbox):
        targets, authorized = self.assignments.snapshot()
        return {"updated_at": int(time.time()), **self.scheduler.metrics(), **outbox.stats(),
                "assignments_authorized": authorized, "target_snapshot_devices": len(targets)}

    def _save_status(self, outbox):
        status = self.status(outbox)
        # Counts and timing only; no addresses, tokens or device credentials.
        temporary = self.path / "status.json.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(status, handle)
        os.replace(temporary, self.path / "status.json")
        log.info("poller status %s", json.dumps(status, sort_keys=True))

    def run(self):
        outbox = Outbox(self.path)
        for target in [self._refresh] + [self._upload] * self.upload_workers:
            thread = threading.Thread(target=self._safe_worker, args=(target,), name="discovery-io")
            thread.start()
            self.threads.append(thread)
        previous = {}
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, lambda *_: self.stop.set())
        last_status = 0
        try:
            while not self.stop.is_set():
                if not self.assignments.ready.wait(0.1):
                    continue
                targets, enabled = self.assignments.snapshot()
                self.scheduler.update(targets)
                try:
                    self.scheduler.step(outbox, enabled=enabled)
                    if time.monotonic() - last_status >= 30:
                        self._save_status(outbox)
                        last_status = time.monotonic()
                except (sqlite3.Error, OSError):
                    log.error("spool unavailable; pausing new collection until storage recovers")
                    self.stop.wait(1)
                    continue
                if self.once:
                    if not enabled:
                        return 1
                    finished = (all(s.attempts for s in self.scheduler.states.values())
                                and not self.scheduler.running and self.scheduler.pending is None
                                and self.scheduler.results.empty())
                    if finished:
                        stats = outbox.stats()
                        with self.assignments.lock:
                            delivery = self.assignments.delivery_healthy
                        if stats["pending_reports"] == 0 or not delivery:
                            self._save_status(outbox)
                            return int(bool(stats["pending_reports"] or stats["quarantined_reports"] or self.scheduler.errors))
                self.stop.wait(0.05)
            return int(self.worker_failed.is_set())
        finally:
            self.stop.set()
            self.scheduler.close()
            for thread in self.threads:
                thread.join()
            outbox.connection.close()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
