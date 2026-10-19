import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
from test_scheduler import report, targets

from discovery.outbox import Outbox
from discovery.runtime import Runtime


def test_blocked_https_upload_does_not_block_device_collection(tmp_path):
    items = targets(80)
    upload_started, release_upload, all_collected = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    collected, result = set(), []

    def collector(target, context, profile, **kwargs):
        with lock:
            collected.add(target.key)
            if len(collected) == len(items):
                all_collected.set()
        return report(target, context, profile)

    def server(request):
        if request.method == "GET":
            return httpx.Response(200, json={"items": [t.model_dump() for t in items]})
        upload_started.set()
        assert release_upload.wait(10)
        return httpx.Response(200, json={"accepted": True})

    runtime = Runtime(tmp_path, {"default": {}}, lambda: httpx.Client(
        base_url="https://test/", transport=httpx.MockTransport(server)), workers=8, upload_workers=2, once=True)
    runtime.scheduler.collector = collector
    thread = threading.Thread(target=lambda: result.append(runtime.run()))
    thread.start()
    try:
        assert upload_started.wait(5)
        assert all_collected.wait(5), "blocked upload stalled unrelated SNMP collection"
    finally:
        release_upload.set()
        thread.join(10)
        if thread.is_alive():
            runtime.stop.set()
            thread.join(5)
    assert not thread.is_alive() and result == [0]
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["completed_polls"] == 80 and status["pending_reports"] == 0


def test_parallel_uploaders_lease_different_reports_and_ack_only_their_rows(tmp_path):
    box = Outbox(tmp_path)
    for target in targets(30):
        box.put(report(target, target.contexts[0], {}))
    seen, active, peak = [], 0, 0
    lock, barrier = threading.Lock(), threading.Barrier(2)

    def server(request):
        nonlocal active, peak
        payload = json.loads(request.content)
        with lock:
            seen.append(payload["report_id"])
            initial = len(seen) <= 2
            active += 1
            peak = max(peak, active)
        if initial:
            barrier.wait(5)
        with lock:
            active -= 1
        return httpx.Response(200, json={"accepted": True})

    def upload(_):
        owned = Outbox(tmp_path)
        try:
            with httpx.Client(base_url="https://test/", transport=httpx.MockTransport(server)) as client:
                assert owned.flush(client)
        finally:
            owned.connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(upload, range(2)))
    assert peak == 2 and len(seen) == len(set(seen)) == 30
    assert box.stats()["pending_reports"] == 0


def test_old_spool_migrates_without_loss_and_abandoned_upload_lease_expires(tmp_path):
    connection = sqlite3.connect(tmp_path / "outbox.sqlite3")
    connection.execute("CREATE TABLE reports(id INTEGER PRIMARY KEY,payload TEXT NOT NULL,rejected INTEGER DEFAULT 0)")
    value = report(targets(1)[0], targets(1)[0].contexts[0], {})
    connection.execute("INSERT INTO reports(payload) VALUES(?)", (value.model_dump_json(),))
    connection.commit()
    connection.close()
    box = Outbox(tmp_path)
    first = box.claim(now=100)
    assert first is not None
    assert box.claim(now=101) is None
    assert box.claim(now=221) == first


def test_revoked_assignment_does_not_collect_from_cached_targets(tmp_path):
    box = Outbox(tmp_path)
    box.save_targets(targets(2))
    runtime = Runtime(tmp_path, {"default": {}}, lambda: httpx.Client(
        base_url="https://test/", transport=httpx.MockTransport(lambda r: httpx.Response(401))), once=True)
    runtime.scheduler.collector = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not collect"))
    assert runtime.run() == 1
    assert box.stats()["pending_reports"] == 0


def test_once_during_api_outage_collects_cached_targets_and_retains_reports(tmp_path):
    box = Outbox(tmp_path)
    box.save_targets(targets(8))
    runtime = Runtime(tmp_path, {"default": {}}, lambda: httpx.Client(
        base_url="https://test/", transport=httpx.MockTransport(lambda r: httpx.Response(503))), once=True)
    runtime.scheduler.collector = report
    assert runtime.run() == 1
    assert box.stats()["pending_reports"] == 8


def test_parallel_delivery_preserves_device_order_without_blocking_other_devices(tmp_path):
    box = Outbox(tmp_path)
    a, b = targets(2)
    first, second, other = (report(a, a.contexts[0], {}), report(a, a.contexts[0], {}), report(b, b.contexts[0], {}))
    for value in (first, second, other):
        box.put(value)
    barrier, lock = threading.Barrier(2), threading.Lock()
    seen = []

    def server(request):
        value = json.loads(request.content)
        with lock:
            seen.append(value["report_id"])
        if value["report_id"] in (first.report_id, other.report_id):
            barrier.wait(5)
        else:
            assert first.report_id in seen and other.report_id in seen
        return httpx.Response(200, json={"accepted": True})

    def upload(_):
        owned = Outbox(tmp_path)
        try:
            with httpx.Client(base_url="https://test/", transport=httpx.MockTransport(server)) as client:
                assert owned.flush(client)
        finally:
            owned.connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(upload, range(2)))
    assert set(seen[:2]) == {first.report_id, other.report_id}
    assert seen[-1] == second.report_id and box.stats()["pending_reports"] == 0


def test_late_ack_cannot_delete_a_new_report_when_sqlite_reuses_a_row_id(tmp_path):
    box = Outbox(tmp_path)
    target = targets(1)[0]
    old, new = report(target, target.contexts[0], {}), report(target, target.contexts[0], {})
    box.put(old)
    original_id = box.connection.execute("SELECT id FROM reports").fetchone()[0]

    def server(request):
        # Model another uploader acknowledging this same report after a lease
        # expiry, followed by a new collection before this reply arrives.
        with box.connection:
            box.connection.execute("DELETE FROM reports")
        box.put(new)
        assert box.connection.execute("SELECT id FROM reports").fetchone()[0] == original_id
        return httpx.Response(200, json={"accepted": True})

    with httpx.Client(base_url="https://test/", transport=httpx.MockTransport(server)) as client:
        assert box.flush(client, limit=1)
    assert box.stats()["pending_reports"] == 1
    assert json.loads(box.connection.execute("SELECT payload FROM reports").fetchone()[0])["report_id"] == new.report_id
