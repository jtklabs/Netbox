"""Bounded per-device scheduling. Workers collect; the caller alone spools.

There is no fleet-wide sweep barrier. A slow device occupies one slot, and its
contexts run serially under one shared deadline. Completed reports flow through
a bounded queue so neither an API outage nor a large fleet queues unlimited RAM.
"""
import hashlib
import logging
import math
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import uuid4

from .collector import collect
from .schemas import Report, Table, Target

log = logging.getLogger("endpoint-poller")


@dataclass
class DeviceSchedule:
    target: Target
    due: float
    started: float = 0
    failures: int = 0
    attempts: int = 0


@dataclass
class Scheduler:
    credentials: dict
    workers: int = 32
    interval: float = 60
    device_timeout: float = 120
    max_backoff: float = 900
    stagger: bool = True
    repeat: bool = True
    collector: object = collect
    clock: object = time.monotonic
    states: dict = field(default_factory=dict, init=False)

    def __post_init__(self):
        if not 1 <= self.workers <= 256 or self.interval <= 0 or self.device_timeout <= 0:
            raise ValueError("invalid worker count, interval, or device budget")
        self.executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="snmp")
        self.results = queue.Queue(maxsize=self.workers)
        self.stop = threading.Event()
        self.running = {}
        self.pending = None
        self.durations = deque(maxlen=1500)
        self.completed = self.reports = self.errors = self.overruns = 0
        self.blocked = False

    def update(self, targets, now=None):
        now = self.clock() if now is None else now
        keys = {t.key for t in targets}
        self.states = {k: v for k, v in self.states.items() if k in keys}
        for target in targets:
            old = self.states.get(target.key)
            if old is None or old.target.revision != target.revision:
                # Stable phases spread startup and steady state without making
                # all 1,500 devices due at the same instant after every restart.
                phase = int.from_bytes(hashlib.sha256(target.key.encode()).digest()[:4], "big") / 2**32
                self.states[target.key] = DeviceSchedule(target, now + (phase * self.interval if self.stagger else 0))

    def _work(self, state):
        failed = False
        deadline = self.clock() + self.device_timeout
        for context in state.target.contexts:
            if self.stop.is_set():
                break
            try:
                profile = self.credentials.get(state.target.credential)
                if profile is None:
                    raise ValueError("missing credential profile")
                report = self.collector(state.target, context, profile, deadline=deadline)
            except Exception as exc:
                # Keep other devices running without exposing exception text,
                # credential values, or raw device replies in logs.
                log.error("device %s collection failed (%s)", state.target.key, type(exc).__name__)
                report = Report(report_id=str(uuid4()), device_key=state.target.key,
                                target_revision=state.target.revision, context=context.key,
                                observed_at=int(time.time()), tables=[
                                    Table(kind=kind, status="error", error="device collection failed")
                                    for kind in context.tables])
            failed |= any(t.status == "error" for t in report.tables)
            while not self.stop.is_set():
                try:
                    self.results.put(report, timeout=0.1)
                    break
                except queue.Full:
                    continue
        return failed

    def step(self, outbox, enabled=True, now=None):
        """One nonblocking coordinator turn; all SQLite access stays here."""
        now = self.clock() if now is None else now
        self.blocked = False
        for _ in range(self.workers * 2):
            if self.pending is None:
                try:
                    self.pending = self.results.get_nowait()
                except queue.Empty:
                    break
            try:
                outbox.put(self.pending)
            except RuntimeError:
                self.blocked = True
                break  # hold this report, pause admission, let uploads free space
            self.pending = None
            self.reports += 1
        for future, state in list(self.running.items()):
            if not future.done():
                continue
            failed = future.result()
            duration = max(0, now - state.started)
            self.durations.append(duration)
            self.completed += 1
            self.errors += int(failed)
            self.overruns += int(duration > self.interval)
            state.failures = state.failures + 1 if failed else 0
            state.attempts += 1
            delay = (max(self.interval, min(self.max_backoff, self.interval * 2 ** min(state.failures, 10)))
                     if failed else self.interval)
            state.due = max(state.started + delay, now + (0.1 if duration >= delay else 0))
            del self.running[future]
        if not enabled or self.blocked or self.stop.is_set():
            return
        try:
            outbox.check_capacity()
        except RuntimeError:
            self.blocked = True
            return
        busy_keys = {s.target.key for s in self.running.values()}
        busy_addresses = {(s.target.address, s.target.snmp_port) for s in self.running.values()}
        for state in sorted(self.states.values(), key=lambda s: (s.due, s.target.key)):
            if len(self.running) >= self.workers:
                break
            address = state.target.address, state.target.snmp_port
            if state.due > now or state.target.key in busy_keys or address in busy_addresses:
                continue
            if not self.repeat and state.attempts:
                continue
            state.started = now
            self.running[self.executor.submit(self._work, state)] = state
            busy_keys.add(state.target.key)
            busy_addresses.add(address)

    def metrics(self, now=None):
        now = self.clock() if now is None else now
        active = {s.target.key for s in self.running.values()}
        lag = [max(0, now - s.due) for k, s in self.states.items() if k not in active and s.due <= now]
        durations = sorted(self.durations)
        mean = sum(durations) / len(durations) if durations else 0
        return {"assigned_devices": len(self.states), "workers": self.workers, "active_devices": len(self.running),
                "due_devices": len(lag), "max_queue_lag_seconds": round(max(lag, default=0), 3),
                "collection_mean_seconds": round(mean, 3),
                "collection_p95_seconds": round(durations[min(len(durations) - 1, math.ceil(len(durations) * .95) - 1)], 3) if durations else 0,
                "completed_polls": self.completed, "failed_polls": self.errors,
                "interval_overruns": self.overruns, "spooled_reports": self.reports,
                "spool_blocked": self.blocked, "interval_seconds": self.interval,
                "estimated_busy_workers": round(len(self.states) * mean / self.interval, 2) if self.interval else 0}

    def close(self):
        self.stop.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
