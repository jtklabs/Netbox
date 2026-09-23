"""Queued remediation: dry run, NetBox's start gate, then apply, one feature at a time."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from netops.upgrade import remediation, scheduler

ONE = 'inventory: NetBox (1 device(s))\nsummary: 1 device(s)\n'


def job(features=('ntp', 'syslog'), mode='add'):
    return {'id': 5, 'device_id': 42, 'hostname': '192.0.2.4', 'device': 'sw1', 'claim_token': str(uuid4()),
            'operation': 'remediate', 'profile': {'features': list(features), 'mode': mode}}


class Recorder:
    def __init__(self, authorize=True):
        self.events, self.authorize = [], authorize

    def __call__(self, stage, message, payload=None):
        self.events.append((stage, message, payload))
        return self.authorize if stage == 'ready' else True

    @property
    def stages(self):
        return [stage for stage, _, _ in self.events]


def fake(outcomes, calls):
    """outcomes: {(feature, apply): (code, output)}"""
    def invoke(feature, device_id, mode, apply, args):
        calls.append((feature, device_id, mode, apply))
        return outcomes[(feature, apply)]
    return invoke


@pytest.mark.parametrize('profile', [{'features': ['nac'], 'mode': 'add'}, {'features': [], 'mode': 'add'},
                                     {'features': ['ntp'], 'mode': 'enforce'}, {'features': ['ntp', 'ntp'], 'mode': 'add'},
                                     {'features': ['ntp'], 'mode': 'add', 'argv': ['--x']}, None])
def test_only_listed_features_and_modes_are_accepted(profile):
    with pytest.raises(ValueError):
        remediation.validate(profile)


def test_assignment_validation_accepts_remediation_only_with_apply():
    assert scheduler.validate_assignment(job(), True) == (['ntp', 'syslog'], 'add')
    with pytest.raises(ValueError, match='without --apply'):
        scheduler.validate_assignment(job(), False)


def test_command_targets_the_one_device_through_netbox_inventory():
    args = SimpleNamespace(env_file='/etc/netops/poll.env', standards=SimpleNamespace(path='/opt/standards.yaml'))
    argv = remediation.command('ntp', 42, 'replace', False, args)
    assert argv[2:] == ['ntp', '--netbox', '--netbox-filter', 'id=42', '--no-netbox-autofilter', '--replace',
                        '--fail-on-diff', '--env-file', '/etc/netops/poll.env', '--standards', '/opt/standards.yaml']
    assert remediation.command('ntp', 42, 'add', True, SimpleNamespace())[-3:] == ['--add', '--apply', '--yes']


def test_compliant_device_is_never_applied(monkeypatch):
    calls, emit = [], Recorder()
    monkeypatch.setattr(remediation, 'invoke', fake({('ntp', False): (0, ONE), ('syslog', False): (0, ONE)}, calls))
    failed, changed, _ = remediation.run_job(None, job(), None, emit)
    assert (failed, changed) == (False, False)
    assert emit.stages == ['connecting', 'precheck_complete', 'already_current']
    assert all(not apply for *_, apply in calls)


def test_only_pending_features_are_applied_after_the_gate(monkeypatch):
    calls, emit = [], Recorder()
    monkeypatch.setattr(remediation, 'invoke', fake({
        ('ntp', False): (2, ONE), ('syslog', False): (0, ONE),
        ('ntp', True): (0, ONE + 'rollback recorded in /opt/rollback/ntp-1.json\n')}, calls))
    failed, changed, results = remediation.run_job(None, job(), None, emit)
    assert (failed, changed) == (False, True)
    assert emit.stages == ['connecting', 'precheck_complete', 'ready', 'completed']
    assert calls[-1] == ('ntp', 42, 'add', True)
    assert results == {'ntp': 'changed', 'syslog': 'compliant'}
    assert emit.events[-1][2]['progress_summary']['rollback'] == ['/opt/rollback/ntp-1.json']


def test_refused_gate_changes_nothing(monkeypatch):
    calls, emit = [], Recorder(authorize=False)
    monkeypatch.setattr(remediation, 'invoke', fake({('ntp', False): (2, ONE)}, calls))
    with pytest.raises(ValueError, match='did not authorize'):
        remediation.run_job(None, job(['ntp']), None, emit)
    assert all(not apply for *_, apply in calls)


def test_dry_run_failure_or_wrong_selection_stops_before_the_gate(monkeypatch):
    calls, emit = [], Recorder()
    monkeypatch.setattr(remediation, 'invoke', fake({('ntp', False): (1, 'error: login failed\n')}, calls))
    assert remediation.run_job(None, job(['ntp']), None, emit)[0] is True
    assert emit.stages == ['connecting', 'failed'] and 'login failed' in emit.events[-1][1]

    emit = Recorder()
    monkeypatch.setattr(remediation, 'invoke', fake({('ntp', False): (0, 'inventory: NetBox (0 device(s))\n')}, calls))
    assert remediation.run_job(None, job(['ntp']), None, emit)[0] is True
    assert 'did not select this device' in emit.events[-1][1]


def test_apply_failure_reports_the_rollback(monkeypatch):
    calls, emit = [], Recorder()
    monkeypatch.setattr(remediation, 'invoke', fake({
        ('ntp', False): (2, ONE), ('ntp', True): (1, ONE + 'rollback recorded in /opt/rb.json\nsw1 FAILED -- verify\n')},
        calls))
    failed, _, _ = remediation.run_job(None, job(['ntp']), None, emit)
    assert failed
    assert emit.stages[-1] == 'failed'
    assert 'configure.py rollback /opt/rb.json' in emit.events[-1][1]


def test_drift_the_feature_will_not_fix_completes_with_warnings(monkeypatch):
    calls, emit = [], Recorder()
    monkeypatch.setattr(remediation, 'invoke', fake({('snmp', False): (2, ONE), ('snmp', True): (2, ONE)}, calls))
    assert remediation.run_job(None, job(['snmp']), None, emit)[0] is False
    assert emit.stages[-1] == 'completed_with_warnings'
