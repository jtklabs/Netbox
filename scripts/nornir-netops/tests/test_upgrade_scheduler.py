"""Scheduled jobs must not turn retries or interrupted cron ticks into installs."""
import copy
import json
import os
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from netops import archive
from netops.netbox import NetBoxError
from netops.upgrade.profile import Profile
from netops.upgrade.progress import Reporter
from netops.upgrade import scheduler


@pytest.fixture
def job():
    profile = Profile('validated-c9350', ('C9350-48P',), ('17.18.1',), '17.18.4',
                      'cisco9k_iosxe.17.18.04.SPA.bin', 'a' * 32, 1500000000)
    data = json.loads(json.dumps(asdict(profile)))
    return {'id': 12, 'device_id': 23, 'hostname': '192.0.2.4', 'device': 'sw1',
            'claim_token': str(uuid4()), 'operation': 'upgrade', 'profile': data}


def test_profile_snapshot_is_validated_without_mutating_api_data(job):
    original = copy.deepcopy(job)
    assert scheduler.validate_assignment(job, True).target_version == '17.18.4'
    assert job == original


@pytest.mark.parametrize('operation', ['audit', 'stage', 'upgrade'])
@pytest.mark.parametrize('model,family', [('WS-C3650-48PS-S', 'C3650'), ('WS-C3850-48P-S', 'C3850')])
def test_cat3k_netbox_profile_assignment(job, operation, model, family):
    job['operation'] = operation
    job['profile'].update(models=[model], starting_versions=['16.12.13'],
                          target_version='16.12.14', image='cat3k_caa-universalk9.16.12.14.SPA.bin')
    if operation == 'stage':
        job['profile']['starting_versions'] = []
    original = copy.deepcopy(job)
    profile = scheduler.validate_assignment(job, operation != 'audit')
    assert profile.family == family
    assert scheduler.platform_for(job['profile']) == 'cisco_ios'
    assert job == original


@pytest.mark.parametrize('operation', ['audit', 'stage', 'upgrade'])
@pytest.mark.parametrize('release', ['17.12.06', '17.12.6'])
def test_c9300_171206_netbox_profile_assignment(job, operation, release):
    job['operation'] = operation
    job['profile'].update(models=['C9300-48P'], starting_versions=[release],
                          target_version='17.12.7', image='cat9k_iosxe.17.12.07.SPA.bin')
    if operation == 'stage':
        job['profile'].update(starting_versions=[], target_version=release,
                              image='cat9k_iosxe.17.12.06.SPA.bin')
    original = copy.deepcopy(job)
    profile = scheduler.validate_assignment(job, operation != 'audit')
    assert profile.family == 'C9300'
    assert profile.release(release) == profile.release('17.12.6')
    assert scheduler.platform_for(job['profile']) == 'cisco_ios'
    assert job == original


@pytest.mark.parametrize('field,value', [('operation', 'shell'), ('hostname', '192.0.2.4;reload'),
                                        ('hostname', ''), ('hostname', None),
                                        ('id', True), ('claim_token', 'invalid')])
def test_malformed_assignment_is_rejected(job, field, value):
    job[field] = value
    with pytest.raises(ValueError):
        scheduler.validate_assignment(job, True)


def test_staging_assignment_may_omit_starting_versions(job):
    job['profile']['starting_versions'] = []
    with pytest.raises(ValueError, match='starting_versions'):
        scheduler.validate_assignment(job, True)
    job['operation'] = 'stage'
    assert scheduler.validate_assignment(job, True).starting_versions == ()


def test_mutating_assignment_requires_local_apply(job):
    with pytest.raises(ValueError, match='without --apply'):
        scheduler.validate_assignment(job, False)
    job['operation'] = 'audit'
    scheduler.validate_assignment(job, False)


def event(stage='completed', sequence=8):
    return {'sequence': sequence, 'stage': stage, 'message': 'Checked', 'run_id': 'run1'}


def test_progress_is_persisted_before_delivery_and_replayed_privately(tmp_path, job, monkeypatch):
    monkeypatch.setattr(scheduler.time, 'sleep', lambda seconds: None)
    client = Mock()
    client.post.side_effect = NetBoxError('offline')
    outbox = scheduler.Outbox(tmp_path, client)
    assert not outbox.sink(job)(event())
    path = next(tmp_path.glob('*.json'))
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())['body']['claim_token'] == job['claim_token']
    assert json.loads(path.read_text())['body']['worker_version'] == scheduler.__version__
    client.post.side_effect = None
    client.post.return_value = {'id': 12, 'sequence': 8, 'status': 'completed'}
    assert outbox.replay()
    assert not list(tmp_path.glob('*.json'))
    assert client.post.call_args.args[1]['sequence'] == 8


def test_ready_is_never_deferred_and_requires_running_acknowledgment(tmp_path, job, monkeypatch):
    monkeypatch.setattr(scheduler.time, 'sleep', lambda seconds: None)
    client = Mock()
    client.post.return_value = {'id': 12, 'sequence': 8, 'status': 'failed'}
    outbox = scheduler.Outbox(tmp_path, client)
    assert not outbox.sink(job)(event('ready'))
    assert not list(tmp_path.glob('*.json'))
    client.post.return_value['status'] = 'running'
    assert outbox.sink(job)(event('ready'))


def test_ready_is_retried_with_the_same_sequence(tmp_path, job, monkeypatch):
    monkeypatch.setattr(scheduler.time, 'sleep', lambda seconds: None)
    client = Mock()
    client.post.side_effect = [NetBoxError('timeout'), {'id': 12, 'sequence': 8, 'status': 'running'}]
    outbox = scheduler.Outbox(tmp_path, client)
    assert outbox.sink(job)(event('ready'))
    assert client.post.call_args_list[0] == client.post.call_args_list[1]


def test_outbox_refuses_to_replay_a_start_gate(tmp_path, job):
    outbox = scheduler.Outbox(tmp_path, Mock())
    outbox.persist(job['id'], {**event('ready'), 'claim_token': job['claim_token']})
    with pytest.raises(ValueError, match='start authorization'):
        outbox.replay()


def test_overlapping_ticks_cannot_claim_another_batch(tmp_path):
    with scheduler.tick_lock(tmp_path):
        with pytest.raises(BlockingIOError):
            with scheduler.tick_lock(tmp_path):
                pytest.fail('second cron tick acquired the lock')
    with scheduler.tick_lock(tmp_path):
        pass


def test_reporter_gate_includes_netbox_authorization(tmp_path):
    run = archive.Run(['upgrade', '--apply'], tmp_path)
    run.args = SimpleNamespace(command='upgrade', apply=True, stage_only=False)
    run.prepare()
    host = SimpleNamespace(name='sw1', hostname='192.0.2.4', platform='cisco_ios', data={'netbox_id': 23})
    sink = Mock(return_value=False)
    reporter = Reporter(run, event_sink=sink)
    assert not reporter.emit(host, 'ready', 'Starting')
    assert reporter.delivery_failed
    assert run.records['sw1']['upgrade_events'][-1]['netbox_delivery'] == 'failed'
    assert 'claim_token' not in json.dumps(run.document)


def test_queue_transport_refuses_redirects():
    client = scheduler.QueueClient('https://netbox.example', 'test', True)
    client.local.session = Mock()
    client.local.session.post.return_value.status_code = 302
    with pytest.raises(NetBoxError, match='HTTP 302'):
        client.post('check-in/', {'name': 'test'})
    assert client.local.session.post.call_args.kwargs['allow_redirects'] is False


def test_cli_default_does_not_enable_apply():
    from netops.cli import build_parser
    args = build_parser().parse_args(['upgrade-poll', '--poller', 'test'])
    assert args.apply is False
    assert args.workers == 3
    assert args.profile is None


def test_poll_help_uses_the_real_entrypoint_without_creating_an_idle_archive(tmp_path, monkeypatch, capsys):
    from netops.cli import main
    monkeypatch.setenv('NETOPS_REPORT_DIR', str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        main(['upgrade-poll', '--no-env-file', '--no-log-file', '--help'])
    assert exc.value.code == 0
    assert 'upgrade-poll' in capsys.readouterr().out
    assert not list(tmp_path.glob('*.json'))


def test_idle_tick_never_loads_device_credentials_or_creates_reports(tmp_path, monkeypatch):
    from netops import cli
    from netops.standards import Standards
    from netops.upgrade import scheduler
    args = cli.build_parser().parse_args(['upgrade-poll', '--poller', 'lab', '--queue-state-dir', str(tmp_path)])
    monkeypatch.setattr('netops.standards.load', lambda *a: Standards())
    monkeypatch.setenv('NETBOX_URL', 'https://netbox.example')
    monkeypatch.setenv('NETBOX_TOKEN', 'test')
    monkeypatch.delenv('NETBOX_SECRET', raising=False)
    args.netbox_secret = None
    client = Mock(url='https://netbox.example')
    client.post.return_value = {'jobs': []}
    monkeypatch.setattr(scheduler, 'QueueClient', Mock(return_value=client))
    login = Mock(side_effect=AssertionError('idle tick loaded SSH credentials'))
    monkeypatch.setattr(cli, '_login', login)
    assert scheduler.run(args, Mock()) == 0
    login.assert_not_called()
    from netops import __version__
    client.post.assert_called_once_with('check-in/', {'name': 'lab', 'limit': 3, 'apply': False,
                                                   'worker_version': __version__, 'supports_cancellation': True})


@pytest.mark.parametrize('operation,apply,stage_only', [('audit', False, False), ('stage', True, True), ('upgrade', True, False)])
def test_each_job_uses_its_operation_and_a_separate_archive(tmp_path, job, monkeypatch, operation, apply, stage_only):
    from nornir.core.task import Result
    from netops.cli import build_parser
    job['operation'] = operation
    args = build_parser().parse_args(['upgrade-poll', '--apply', '--poller', 'lab', '--report-dir', str(tmp_path)])
    monkeypatch.setattr(scheduler, 'settings_from_env', lambda: None)
    client = Mock()
    client.post.side_effect = lambda path, body: {'id': 12, 'sequence': body.get('sequence', 0), 'status':
        'claimed' if body.get('stage') == 'queued' else 'running' if body.get('stage') == 'ready' else 'completed'}
    host = SimpleNamespace(name='sw1', hostname=job['hostname'], platform='cisco_ios',
                           data={'upgrade_job': job, 'netbox_id': job['device_id']})
    task = SimpleNamespace(host=host)
    def workflow(task, profile, options, reporter):
        assert (options.apply, options.stage_only) == (apply, stage_only)
        assert reporter.emit(host, 'completed' if apply else 'dry_run_complete', 'Finished')
        return Result(host=host, result={}, changed=apply)
    monkeypatch.setattr(scheduler, 'upgrade_device', workflow)
    result = scheduler.execute_job(task, args, client, scheduler.Outbox(tmp_path / 'spool', client))
    assert not result.failed
    documents = [json.loads(path.read_text()) for path in tmp_path.glob('*.json')]
    assert len(documents) == 1
    assert documents[0]['worker_version'] == scheduler.__version__
    assert documents[0]['dry_run'] is not apply
    assert documents[0]['schedule']['job_id'] == 12
    assert job['claim_token'] not in json.dumps(documents)


@pytest.mark.parametrize('operation', ['audit', 'stage', 'upgrade', 'audit_config', 'remediate'])
@pytest.mark.parametrize('denial', ['failed', 'completed', 'pending', 'offline'])
def test_connection_requires_live_authorization_for_every_operation(tmp_path, job, monkeypatch, operation, denial):
    from netops.cli import build_parser
    job['operation'] = operation
    if operation in ('audit_config', 'remediate'):
        job['profile'] = {'features': ['ntp'], 'mode': 'replace'}
    args = build_parser().parse_args(['upgrade-poll', '--apply', '--poller', 'lab', '--report-dir', str(tmp_path)])
    monkeypatch.setattr(scheduler, 'settings_from_env', lambda: None)
    monkeypatch.setattr(scheduler.time, 'sleep', lambda seconds: None)
    client = Mock()
    def response(path, body):
        if body.get('stage') == 'queued' and denial == 'offline':
            raise NetBoxError('unreachable')
        return {'id': job['id'], 'sequence': body.get('sequence', 0), 'status': denial}
    client.post.side_effect = response
    host = SimpleNamespace(name='sw1', hostname=job['hostname'], platform='cisco_ios',
                           data={'upgrade_job': job, 'netbox_id': job['device_id']})
    upgrade = Mock(side_effect=AssertionError('Device work must not start'))
    remediate = Mock(side_effect=AssertionError('Device work must not start'))
    monkeypatch.setattr(scheduler, 'upgrade_device', upgrade)
    monkeypatch.setattr(scheduler.remediation, 'run_job', remediate)
    spool = tmp_path / 'spool'
    result = scheduler.execute_job(SimpleNamespace(host=host), args, client, scheduler.Outbox(spool, client))
    assert result.failed
    upgrade.assert_not_called()
    remediate.assert_not_called()
    assert not any(json.loads(path.read_text())['body']['stage'] == 'queued' for path in spool.glob('*.json'))


def queue_job(number):
    return {'id': number, 'device_id': number, 'device': f'device-{number}',
            'hostname': f'192.0.2.{number}', 'claim_token': str(uuid4()), 'profile': {}}


def queue_args(workers=2):
    return SimpleNamespace(workers=workers, poller='lab', apply=True, poll_interval=0.01)


def queue_login():
    return SimpleNamespace(username='test', password='test-password', secret=None), 0


def test_free_slots_pick_up_later_jobs_and_refill_while_long_job_runs(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    first_started, second_started, third_started = (threading.Event() for _ in range(3))
    release_first, release_second = threading.Event(), threading.Event()
    running, maximum, calls = set(), [], []
    lock = threading.Lock()
    jobs = [queue_job(i) for i in (1, 2, 3)]

    def claim(path, body):
        calls.append(body['limit'])
        if len(calls) == 1:
            return {'jobs': [jobs[0]]}
        assert first_started.wait(2)
        if len(calls) == 2:
            return {'jobs': []}  # Nothing scheduled yet; keep polling the free slot.
        if len(calls) == 3:
            return {'jobs': [jobs[1]]}
        if len(calls) == 4:
            assert not release_first.is_set()
            return {'jobs': [jobs[2]]}
        return {'jobs': []}

    def execute(job, *args):
        with lock:
            running.add(job['id'])
            maximum.append(len(running))
        try:
            if job['id'] == 1:
                first_started.set()
                assert release_first.wait(5)
            elif job['id'] == 2:
                second_started.set()
                assert release_second.wait(5)
            else:
                third_started.set()
            return False
        finally:
            with lock:
                running.remove(job['id'])

    monkeypatch.setattr(scheduler, 'run_claimed_job', execute)
    client, outbox, login = Mock(), Mock(), Mock(side_effect=queue_login)
    client.post.side_effect = claim
    outbox.replay.return_value = True
    with ThreadPoolExecutor(max_workers=1) as supervisor:
        result = supervisor.submit(scheduler.run_queue, queue_args(), client, outbox, login)
        try:
            assert second_started.wait(3), 'new job was not picked up while slot was free'
            assert not release_first.is_set()
            release_second.set()
            assert third_started.wait(3), 'completed slot was not refilled'
        finally:
            release_first.set()
            release_second.set()
        assert result.result(timeout=3) == 0
    assert max(maximum) == 2
    assert calls[:4] == [2, 1, 1, 1]
    login.assert_called_once()
    assert any(call.kwargs['exclude_job_ids'] == {1} for call in outbox.replay.call_args_list)


def test_outbox_skips_active_job_files_before_reading_them(tmp_path, job):
    outbox = scheduler.Outbox(tmp_path, Mock())
    active = tmp_path / f'{job["id"]}-0000000001.json'
    active.write_text('in-flight file must not be read')
    outbox.persist(99, {**event(), 'claim_token': str(uuid4())})
    outbox.client.post.return_value = {'id': 99, 'sequence': 8}
    assert outbox.replay(exclude_job_ids={job['id']})
    assert active.exists()
    outbox.client.post.assert_called_once()
    assert outbox.client.post.call_args.args[0] == '99/report/'


def test_queue_outage_and_undelivered_progress_do_not_interrupt_active_job(monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    jobs = [queue_job(1), queue_job(2)]
    calls, replays = [], []

    def replay(**kwargs):
        replays.append(kwargs)
        return len(replays) != 3

    def claim(path, body):
        calls.append(body)
        if len(calls) == 1:
            return {'jobs': [jobs[0]]}
        if len(calls) == 2:
            assert started.wait(2)
            raise NetBoxError('temporarily offline')
        if len(calls) == 3:
            assert not release.is_set()
            return {'jobs': [jobs[1]]}
        return {'jobs': []}

    def execute(job, *args):
        if job['id'] == 1:
            started.set()
            assert release.wait(4)
        else:
            release.set()
        return False

    monkeypatch.setattr(scheduler, 'run_claimed_job', execute)
    client, outbox = Mock(), Mock()
    client.post.side_effect = claim
    outbox.replay.side_effect = replay
    try:
        assert scheduler.run_queue(queue_args(), client, outbox, queue_login) == 0
    finally:
        release.set()
    assert len(replays) > len(calls)
    assert all(call['limit'] == 1 for call in calls[1:3])


@pytest.mark.parametrize('worker_failure', [True, RuntimeError('worker failed')])
def test_failed_job_frees_slot_without_abandoning_other_work(monkeypatch, worker_failure):
    jobs, executed = [queue_job(1), queue_job(2)], []
    client, outbox = Mock(), Mock()
    client.post.side_effect = [{'jobs': [jobs[0]]}, {'jobs': [jobs[1]]}, {'jobs': []}]
    outbox.replay.return_value = True

    def execute(job, *args):
        executed.append(job['id'])
        if job['id'] == 1:
            if isinstance(worker_failure, Exception):
                raise worker_failure
            return worker_failure
        return False

    monkeypatch.setattr(scheduler, 'run_claimed_job', execute)
    assert scheduler.run_queue(queue_args(workers=1), client, outbox, queue_login) == 1
    assert executed == [1, 2]


def test_undelivered_completed_progress_blocks_new_claims(monkeypatch):
    client, outbox, login = Mock(), Mock(), Mock()
    outbox.replay.return_value = False
    assert scheduler.run_queue(queue_args(), client, outbox, login) == 1
    client.post.assert_not_called()
    login.assert_not_called()


def test_queue_rejects_over_capacity_response_before_starting_any_job(monkeypatch):
    client, outbox, login = Mock(), Mock(), Mock()
    client.post.return_value = {'jobs': [queue_job(1), queue_job(2)]}
    outbox.replay.return_value = True
    execute = Mock()
    monkeypatch.setattr(scheduler, 'run_claimed_job', execute)
    with pytest.raises(NetBoxError, match='invalid batch'):
        scheduler.run_queue(queue_args(workers=1), client, outbox, login)
    execute.assert_not_called()
    login.assert_not_called()


@pytest.mark.parametrize('failure', [False, True, RuntimeError('task runner failed')])
def test_each_claim_has_isolated_inventory_and_closes_failed_connections(monkeypatch, failure):
    from netops import inventory
    args = SimpleNamespace(key_file=None, port=22, conn_timeout=30)
    job = queue_job(1)
    host = SimpleNamespace(data={})
    nr = Mock()
    nr.inventory.hosts = {'device-1 [job 1]': host}
    if isinstance(failure, Exception):
        nr.run.side_effect = failure
    else:
        nr.run.return_value.failed = failure
    initialize = Mock(return_value=nr)
    monkeypatch.setattr(inventory, 'init_from_records', initialize)
    monkeypatch.setattr(inventory, 'missing_credentials', Mock(return_value=[]))
    credentials, _ = queue_login()
    if isinstance(failure, Exception):
        with pytest.raises(RuntimeError, match='task runner failed'):
            scheduler.run_claimed_job(job, args, credentials, Mock(), Mock())
    else:
        assert scheduler.run_claimed_job(job, args, credentials, Mock(), Mock()) is failure
    assert initialize.call_args.args[-2] == 1
    assert host.data['upgrade_job'] == job
    assert host.data['netbox_id'] == 1
    child_args = nr.run.call_args.kwargs['args']
    assert child_args is not args
    assert child_args.device_credentials is credentials
    assert not hasattr(args, 'device_credentials')
    nr.close_connections.assert_called_once_with(on_good=True, on_failed=True)


def test_heartbeat_reports_the_running_worker_version(job):
    client = Mock()
    heartbeat = scheduler.Heartbeat(client, job)
    heartbeat.stop = Mock()
    heartbeat.stop.wait.side_effect = [False, True]
    heartbeat.loop()
    client.post.assert_called_once_with(f'{job["id"]}/report/', {
        'claim_token': job['claim_token'], 'heartbeat': True,
        'worker_version': scheduler.__version__,
    })


def test_replaying_old_progress_keeps_original_version(tmp_path, job):
    client = Mock()
    client.post.return_value = {'id': job['id'], 'sequence': 8}
    outbox = scheduler.Outbox(tmp_path, client)
    body = {**event(), 'claim_token': job['claim_token'], 'worker_version': '0.1.9'}
    outbox.persist(job['id'], body)
    assert outbox.replay()
    assert client.post.call_args.args[1]['worker_version'] == '0.1.9'
