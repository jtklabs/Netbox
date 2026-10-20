"""Rollback sends one direct reboot to the recorded volume, never a drain/install."""
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests

from netops import cancellation
from netops.upgrade import f5_boot_rollback as rollback, scheduler
from test_upgrade_f5 import FakeBigIP, profile, options, iso, run_unit  # noqa: F401

PLAN = {'volume': 'HD1.1', 'from_volume': 'HD1.2', 'previous_version': '17.1.1.3',
        'previous_build': '0.0.5', 'expected_version': '17.5.1.8'}


@pytest.fixture
def unit(monkeypatch):
    unit = FakeBigIP(version='17.5.1.8')
    unit.volumes[0].update(active=False, version='17.1.1.3')
    unit.volumes[1].update(active=True, version='17.5.1.8', build='0.0.19')
    monkeypatch.setattr(rollback.f5_waf, 'Client', unit)
    return unit


def run(options, reporter=None, plan=None):
    options.apply = True
    host = SimpleNamespace(name='bigip', hostname='192.0.2.10', username='test', password='test', platform='f5_tmsh')
    reporter = reporter or Mock()
    reporter.emit.return_value = True
    return rollback.run(SimpleNamespace(host=host), plan or dict(PLAN), options, reporter)


def test_direct_reboot_without_drain_failover_save_or_install(unit, options):
    result = run(options)
    assert not result.failed and result.changed
    assert unit.rebooted_to == 'HD1.1'
    writes = [(method, path, body) for method, path, body in unit.calls if method != 'GET']
    assert writes == [('POST', '/mgmt/tm/sys', {'command': 'reboot', 'volume': 'HD1.1'})]
    assert unit.saved == unit.failovers == 0


def test_already_on_old_volume_does_not_reboot(unit, options):
    unit.version = '17.1.1.3'
    unit.volumes[0]['active'], unit.volumes[1]['active'] = True, False
    result = run(options)
    assert not result.failed and not result.changed
    assert unit.rebooted_to is None


@pytest.mark.parametrize('change', ['missing', 'incomplete', 'version', 'build', 'active', 'release'])
def test_refuses_changed_or_unusable_partitions(unit, options, change):
    if change == 'missing': unit.volumes.pop(0)
    elif change == 'incomplete': unit.volumes[0]['status'] = 'installing'
    elif change == 'version': unit.volumes[0]['version'] = '16.1.4'
    elif change == 'build': unit.volumes[0]['build'] = '0.0.99'
    elif change == 'active': unit.volumes[1]['name'] = 'HD1.3'
    elif change == 'release': unit.volumes[1]['version'] = '17.5.1.10'
    with pytest.raises(ValueError):
        run(options)
    assert unit.rebooted_to is None
    assert not any(method != 'GET' for method, _, _ in unit.calls)


def test_live_authorization_is_required_before_reboot(unit, options):
    reporter = Mock()
    reporter.emit.side_effect = lambda host, stage, *a, **kw: stage != 'ready'
    with pytest.raises(ValueError, match='did not authorize'):
        run(options, reporter)
    assert unit.rebooted_to is None


def test_ambiguous_reboot_is_never_resent(unit, options):
    options.reload_timeout = 0.05
    original = unit._post
    def post(path, payload):
        if payload.get('command') == 'reboot':
            raise requests.ReadTimeout('unknown outcome')
        return original(path, payload)
    unit._post = post
    with pytest.raises(TimeoutError, match='not confirmed'):
        run(options)
    assert sum(method == 'POST' and body.get('command') == 'reboot' for method, _, body in unit.calls) == 1


def test_cancellation_before_reboot_sends_no_command(unit, options):
    control = cancellation.Control()
    reporter = Mock()
    def report(host, stage, *a, **kw):
        if stage == 'ready': control.requested.set()
        return True
    reporter.emit.side_effect = report
    # The real transport and reboot dispatch each honor the job's checkpoint.
    with cancellation.bind(control), pytest.raises(cancellation.Cancelled):
        run(options, reporter)
    assert unit.rebooted_to is None


def test_scheduler_validates_f5_and_source_confirmation(profile):
    job = {'id': 12, 'device_id': 23, 'device': 'bigip', 'hostname': '192.0.2.10', 'claim_token': str(uuid4()),
           'operation': 'f5_rollback', 'rollback_of': 10, 'rollback_plan': dict(PLAN), 'profile': json.loads(json.dumps(asdict(profile)))}
    assert scheduler.validate_assignment(job, True)['volume'] == 'HD1.1'
    with pytest.raises(ValueError, match='without --apply'):
        scheduler.validate_assignment(job, False)
    for plan in ({}, {**PLAN, 'volume': 'HD1.1; reboot'}, {**PLAN, 'from_volume': 'HD1.1'},
                 {**PLAN, 'expected_version': '16.1.4'}):
        job['rollback_plan'] = plan
        with pytest.raises(ValueError): scheduler.validate_assignment(job, True)


def test_upgrade_reports_previous_partition_even_without_draining(profile, options, unit):
    # Start a normal upgrade on the old partition and inspect its published plan.
    unit.volumes[0]['active'], unit.volumes[1]['active'] = True, False
    unit.version = '17.1.1.3'
    result, reporter, _ = run_unit(profile, options)
    records = [call.args[3]['progress_summary']['rollback_plan'] for call in reporter.emit.call_args_list
               if len(call.args) > 3 and 'rollback_plan' in call.args[3].get('progress_summary', {})]
    assert records
    assert records[0]['volume'] == 'HD1.1'
    assert records[0]['from_volume'] == 'HD1.2'
    assert records[0]['previous_version'] == '17.1.1.3'


def test_active_partition_flag_without_matching_running_release_is_not_success(unit, options):
    unit.volumes[0]['active'], unit.volumes[1]['active'] = True, False
    reporter = Mock()
    with pytest.raises(ValueError, match='running software disagree'):
        run(options, reporter)
    assert unit.rebooted_to is None
    assert not any(call.args[1] in ('completed', 'already_current') for call in reporter.emit.call_args_list)


def test_unrelated_incomplete_partition_does_not_block_boot_recovery(unit, options):
    unit.volumes.append({'name': 'HD1.3', 'active': False, 'status': 'failed'})
    assert not run(options).failed
    assert unit.rebooted_to == 'HD1.1'
