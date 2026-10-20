"""A stop must unwind one job, including long waits and its own feature child."""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops import cancellation
from netops.f5_waf import Client
from netops.upgrade import f5_requests, scheduler
from netops.netbox import NetBoxError
from test_upgrade_scheduler import job  # noqa: F401


def test_stop_interrupts_long_wait_without_stopping_another_thread():
    stop, other = cancellation.Control(), cancellation.Control()
    ready = threading.Event()
    def wait():
        with cancellation.bind(stop):
            ready.set()
            with pytest.raises(cancellation.Cancelled):
                cancellation.sleep(4800)
        cancellation.checkpoint()  # binding is removed after unwind
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelled = pool.submit(wait)
        assert ready.wait(2)
        stop.observe({'cancel_requested': True})
        cancelled.result(timeout=2)
        with cancellation.bind(other):
            cancellation.checkpoint()
            cancellation.sleep(0.001)
        assert not other.requested.is_set()


def test_cancelled_request_never_reaches_device_or_retries_authentication():
    client = Client(SimpleNamespace(hostname='192.0.2.1', username='test', password='test'))
    client.session = Mock()
    control = cancellation.Control()
    control.requested.set()
    with cancellation.bind(control), pytest.raises(cancellation.Cancelled):
        client.request('POST', '/mgmt/tm/sys/reboot', {})
    client.session.request.assert_not_called()


def test_waf_wait_does_not_retry_cancellation_or_wait_for_profile_deadline():
    control = cancellation.Control()
    client = Mock(timeout=30)
    def request(*args, **kwargs):
        control.requested.set()
        raise RuntimeError('401 while service starts')
    client.request.side_effect = request
    plan = {'approved_profile': {'validation_timeout': 4800},
            'waf_requests': {'after': '2026-10-20T10:00:00+00:00'}}
    with cancellation.bind(control), pytest.raises(cancellation.Cancelled):
        f5_requests.wait_for_request(client,
            {'waf': {'provisioned': True, 'policy_count': 1}, 'facts': {'failover_state': 'active'}},
            plan, SimpleNamespace(validation_timeout=4800, poll_interval=600), Mock())
    assert client.request.call_count == 1
    assert plan['waf_requests']['status'] != 'timed_out'


def test_child_is_reaped_without_stopping_another_job(tmp_path):
    first, second = cancellation.Control(), cancellation.Control()
    paths = [tmp_path / 'first.pid', tmp_path / 'second.pid']
    script = 'import os,sys,time; open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(60)'
    def run(control, path):
        with cancellation.bind(control), pytest.raises(cancellation.Cancelled):
            cancellation.run([sys.executable, '-c', script, str(path)], timeout=120)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, c, p) for c, p in zip((first, second), paths)]
        try:
            deadline = time.monotonic() + 5
            while not all(p.exists() and p.read_text() for p in paths):
                assert time.monotonic() < deadline
                time.sleep(0.01)
            pids = [int(p.read_text()) for p in paths]
            first.requested.set()
            futures[0].result(timeout=8)
            with pytest.raises(ProcessLookupError):
                os.kill(pids[0], 0)
            os.kill(pids[1], 0)
            assert not futures[1].done()
        finally:
            first.requested.set()
            second.requested.set()
            for future in futures:
                future.result(timeout=8)


@pytest.mark.parametrize('stage', ['queued', 'ready', 'completed'])
def test_scheduler_unwinds_before_acknowledging_stop(tmp_path, job, monkeypatch, stage):
    from netops.cli import build_parser
    from nornir.core.task import Result
    args = build_parser().parse_args(['upgrade-poll', '--apply', '--poller', 'lab', '--report-dir', str(tmp_path)])
    monkeypatch.setattr(scheduler, 'settings_from_env', lambda: None)
    host = SimpleNamespace(name='sw1', hostname=job['hostname'], platform='cisco_ios',
                           data={'upgrade_job': job, 'netbox_id': job['device_id']})
    entered, unwound, events = [], [], []
    def workflow(task, profile, options, reporter):
        entered.append(True)
        try:
            reporter.emit(host, 'ready', 'Ready')
            reporter.emit(host, 'completed', 'Done')
            return Result(host=host)
        finally:
            unwound.append(True)
    monkeypatch.setattr(scheduler, 'upgrade_device', workflow)
    def response(path, body):
        current = body.get('stage')
        events.append(current)
        if current == 'cancelled':
            assert stage == 'queued' or unwound
        return {'id': job['id'], 'sequence': body.get('sequence', 0),
                'status': 'claimed' if current == 'queued' or (current == 'ready' and stage == 'ready') else 'running',
                'cancel_requested': current == stage}
    client = Mock()
    client.post.side_effect = response
    result = scheduler.execute_job(SimpleNamespace(host=host), args, client, scheduler.Outbox(tmp_path/'spool', client))
    assert result.failed
    assert events[-1] == 'cancelled'
    assert 'failed' not in events
    assert bool(entered) is (stage != 'queued')
    assert not list((tmp_path/'spool').glob('*.json'))
    document = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert 'cancelled' in json.dumps(document)


def test_acknowledgment_is_durable_until_netbox_returns(tmp_path, job, monkeypatch):
    monkeypatch.setattr(scheduler.time, 'sleep', lambda seconds: None)
    client = Mock()
    client.post.side_effect = NetBoxError('offline')
    outbox = scheduler.Outbox(tmp_path, client)
    assert not outbox.sink(job)({'sequence': 4, 'stage': 'cancelled', 'message': 'Stopped', 'run_id': 'run'})
    saved = list(tmp_path.glob('*.json'))
    assert len(saved) == 1
    client.post.side_effect = None
    client.post.return_value = {'id': job['id'], 'sequence': 4, 'status': 'recovery_required', 'cancel_requested': False}
    assert outbox.replay()
    assert not list(tmp_path.glob('*.json'))


def test_heartbeat_only_signals_its_own_control(job):
    control = cancellation.Control()
    client = Mock()
    client.post.return_value = {'cancel_requested': True, 'status': 'running'}
    heartbeat = scheduler.Heartbeat(client, job, control)
    heartbeat.stop = Mock()
    heartbeat.stop.wait.side_effect = [False, True]
    heartbeat.loop()
    assert control.requested.is_set()
    cancellation.checkpoint()


def test_cancel_while_reading_prompt_does_not_send_install_command():
    from netops.upgrade.workflow import Device
    control = cancellation.Control()
    device = Device(Mock(), Mock(), Mock())
    device.connection = Mock()
    def prompt():
        control.requested.set()
        return 'switch#'
    device.connection.find_prompt.side_effect = prompt
    with cancellation.bind(control), pytest.raises(cancellation.Cancelled):
        device.interactive('install add activate commit', 120, reload=True)
    device.connection.write_channel.assert_not_called()
