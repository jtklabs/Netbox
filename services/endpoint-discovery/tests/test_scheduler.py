import threading
import time
from collections import Counter
from uuid import uuid4

from discovery.scheduler import Scheduler
from discovery.schemas import Report, Target


def targets(count):
    return [Target(key=f"device-{i:04}", scope="a/site", poller="p", address=f"10.1.{i // 254}.{i % 254 + 1}",
                   name=f"device-{i}", revision="0" * 64) for i in range(count)]


def report(target, context, profile, **kwargs):
    return Report(report_id=str(uuid4()), device_key=target.key, target_revision=target.revision,
                  context=context.key, observed_at=int(time.time()), tables=[
                      {"kind": kind, "status": "complete", "entries": []} for kind in context.tables])


class Spool:
    def __init__(self):
        self.reports, self.threads, self.full = [], set(), False

    def put(self, value):
        self.check_capacity()
        self.threads.add(threading.get_ident())
        self.reports.append(value)

    def check_capacity(self):
        if self.full:
            raise RuntimeError("full")


def pump(scheduler, spool, predicate, now=None):
    deadline = time.monotonic() + 10
    while not predicate():
        scheduler.step(spool, now=now)
        assert time.monotonic() < deadline, "scheduler did not make expected progress"
        time.sleep(0.001)
    scheduler.step(spool, enabled=False, now=now)


def test_1500_devices_are_bounded_parallel_and_spooled_by_coordinator():
    workers, active, peak, entered = 32, 0, 0, 0
    lock, barrier = threading.Lock(), threading.Barrier(32)

    def collect(target, context, profile, **kwargs):
        nonlocal active, peak, entered
        with lock:
            active += 1
            peak = max(peak, active)
            entered += 1
            initial = entered <= workers
        if initial:
            barrier.wait(timeout=5)
        result = report(target, context, profile)
        with lock:
            active -= 1
        return result

    spool = Spool()
    scheduler = Scheduler({"default": {}}, workers=workers, repeat=False, stagger=False, collector=collect)
    try:
        scheduler.update(targets(1500))
        pump(scheduler, spool, lambda: scheduler.completed == 1500 and len(spool.reports) == 1500)
        assert peak == workers
        assert Counter(r.device_key for r in spool.reports) == Counter(t.key for t in targets(1500))
        assert spool.threads == {threading.get_ident()}
        assert scheduler.metrics()["assigned_devices"] == 1500
    finally:
        scheduler.close()


def test_slow_device_does_not_block_other_device_repolls_and_contexts_share_deadline():
    slow_release, slow_started = threading.Event(), threading.Event()
    calls, deadlines = Counter(), []
    clock = [0.0]
    items = targets(2)

    def collect(target, context, profile, **kwargs):
        calls[target.key] += 1
        deadlines.append(kwargs["deadline"])
        if target.key == items[0].key:
            slow_started.set()
            assert slow_release.wait(5)
        return report(target, context, profile)

    spool = Spool()
    scheduler = Scheduler({"default": {}}, workers=2, interval=30, stagger=False, collector=collect,
                          clock=lambda: clock[0])
    try:
        scheduler.update(items)
        pump(scheduler, spool, lambda: calls[items[1].key] == 1 and scheduler.completed == 1)
        assert slow_started.is_set()
        clock[0] = 30
        pump(scheduler, spool, lambda: calls[items[1].key] == 2)
        assert calls[items[0].key] == 1
        assert deadlines[:2] == [120, 120]
    finally:
        slow_release.set()
        scheduler.close()


def test_start_to_start_schedule_failure_backoff_and_no_overlap():
    clock, starts = [0.0], []
    spool = Spool()

    def collect(target, context, profile, **kwargs):
        starts.append(clock[0])
        value = report(target, context, profile)
        if len(starts) == 1:
            value.tables[0].status = "error"
        return value

    scheduler = Scheduler({"default": {}}, workers=4, interval=30, stagger=False, collector=collect,
                          clock=lambda: clock[0])
    try:
        scheduler.update(targets(1))
        pump(scheduler, spool, lambda: scheduler.completed == 1)
        clock[0] = 30
        scheduler.step(spool)
        assert starts == [0]
        clock[0] = 60
        pump(scheduler, spool, lambda: scheduler.completed == 2)
        clock[0] = 90
        pump(scheduler, spool, lambda: scheduler.completed == 3)
        assert starts == [0, 60, 90]
    finally:
        scheduler.close()


def test_full_spool_pauses_admission_without_dropping_pending_result():
    spool = Spool()
    scheduler = Scheduler({"default": {}}, workers=1, repeat=False, stagger=False, collector=report)
    try:
        scheduler.update(targets(2))
        scheduler.step(spool)
        deadline = time.monotonic() + 5
        while scheduler.results.empty():
            assert time.monotonic() < deadline
            time.sleep(.001)
        spool.full = True
        scheduler.step(spool)
        assert scheduler.blocked and scheduler.pending is not None
        assert not spool.reports
        spool.full = False
        pump(scheduler, spool, lambda: len(spool.reports) == 2 and scheduler.completed == 2)
    finally:
        scheduler.close()


def test_assignment_change_cannot_overlap_inflight_device_and_removal_stops_repolls():
    started, release = threading.Event(), threading.Event()
    calls = []

    def collect(target, context, profile, **kwargs):
        calls.append(target.revision)
        started.set()
        assert release.wait(5)
        return report(target, context, profile)

    spool = Spool()
    scheduler = Scheduler({"default": {}}, workers=2, repeat=False, stagger=False, collector=collect)
    item = targets(1)[0]
    try:
        scheduler.update([item])
        scheduler.step(spool)
        assert started.wait(2)
        changed = item.model_copy(update={"revision": "1" * 64})
        scheduler.update([changed])
        scheduler.step(spool)
        assert calls == ["0" * 64]
        release.set()
        pump(scheduler, spool, lambda: scheduler.completed == 2 and len(spool.reports) == 2)
        assert calls == ["0" * 64, "1" * 64]
        scheduler.update([])
        scheduler.step(spool, now=time.monotonic() + 1000)
        assert not scheduler.running and not scheduler.states
    finally:
        release.set()
        scheduler.close()


def test_duplicate_management_addresses_are_not_polled_concurrently():
    items = targets(2)
    items[1].address = items[0].address
    active, peak = 0, 0
    lock = threading.Lock()

    def collect(target, context, profile, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        time.sleep(.01)
        with lock:
            active -= 1
        return report(target, context, profile)

    spool = Spool()
    scheduler = Scheduler({"default": {}}, workers=2, repeat=False, stagger=False, collector=collect)
    try:
        scheduler.update(items)
        pump(scheduler, spool, lambda: scheduler.completed == 2 and len(spool.reports) == 2)
        assert peak == 1
    finally:
        scheduler.close()


def test_contexts_share_one_device_deadline_and_capacity_lag_is_visible():
    from discovery.schemas import Context
    clock, deadlines = [0.0], []
    item = targets(1)[0]
    item.contexts = [Context(key="one"), Context(key="two")]

    def collect(target, context, profile, **kwargs):
        deadlines.append(kwargs["deadline"])
        clock[0] += 20
        return report(target, context, profile)

    scheduler = Scheduler({"default": {}}, workers=1, interval=30, repeat=False, stagger=False,
                          collector=collect, clock=lambda: clock[0])
    spool = Spool()
    try:
        scheduler.update([item])
        pump(scheduler, spool, lambda: scheduler.completed == 1)
        assert deadlines == [120, 120]
        metrics = scheduler.metrics()
        assert metrics["interval_overruns"] == 1
        assert metrics["collection_p95_seconds"] == 40
        assert metrics["estimated_busy_workers"] > 1
    finally:
        scheduler.close()
