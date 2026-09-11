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


@pytest.mark.parametrize('field,value', [('operation', 'shell'), ('hostname', '192.0.2.4;reload'),
                                        ('id', True), ('claim_token', 'invalid')])
def test_malformed_assignment_is_rejected(job, field, value):
    job[field] = value
    with pytest.raises(ValueError):
        scheduler.validate_assignment(job, True)


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
    client.post.assert_called_once_with('check-in/', {'name': 'lab', 'limit': 3, 'apply': False})


@pytest.mark.parametrize('operation,apply,stage_only', [('audit', False, False), ('stage', True, True), ('upgrade', True, False)])
def test_each_job_uses_its_operation_and_a_separate_archive(tmp_path, job, monkeypatch, operation, apply, stage_only):
    from nornir.core.task import Result
    from netops.cli import build_parser
    job['operation'] = operation
    args = build_parser().parse_args(['upgrade-poll', '--apply', '--poller', 'lab', '--report-dir', str(tmp_path)])
    monkeypatch.setattr(scheduler, 'settings_from_env', lambda: None)
    client = Mock()
    client.post.side_effect = lambda path, body: {'id': 12, 'sequence': body.get('sequence', 0), 'status': 'running' if body.get('stage') == 'ready' else 'completed'}
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
    assert documents[0]['dry_run'] is not apply
    assert documents[0]['schedule']['job_id'] == 12
    assert job['claim_token'] not in json.dumps(documents)
