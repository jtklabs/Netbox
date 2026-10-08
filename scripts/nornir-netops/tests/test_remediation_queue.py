"""Queued remediation: dry run, NetBox's start gate, then apply, one feature at a time."""
from types import SimpleNamespace
import json
from pathlib import Path
from uuid import uuid4

import pytest

from netops.upgrade import remediation, scheduler

ONE = 'inventory: NetBox (1 device(s))\nsummary: 1 device(s)\n'


def test_structured_findings_are_forwarded_without_raw_output(monkeypatch, tmp_path):
    monkeypatch.setenv('NETOPS_REPORT_DIR', str(tmp_path))

    def execute(argv, **kwargs):
        report = Path(argv[argv.index('--report') + 1])
        report.write_text(json.dumps({'devices': {'sw1': {
            'desired': ['192.0.2.10'], 'current': ['ntp server 192.0.2.99'],
            'add': ['192.0.2.10'], 'commands': ['ntp server 192.0.2.10'],
            'output': 'unfiltered diagnostic must never be forwarded',
        }}}))
        return SimpleNamespace(returncode=2, stdout=ONE, stderr='')

    monkeypatch.setattr(remediation.subprocess, 'run', execute)
    emit = Recorder()
    remediation.run_job(None, {**job(['ntp']), 'operation': 'audit_config'}, None, emit)
    summary = emit.events[-1][2]['progress_summary']
    assert summary['compliance_details']['ntp']['commands'] == ['ntp server 192.0.2.10']
    assert 'unfiltered' not in json.dumps(summary)
    assert len(list(tmp_path.glob('queued-*.json'))) == 1


def test_details_are_bounded_and_redacted():
    from netops.debuglog import protect
    protect(['unique-test-password'])
    details = remediation.report_details({'devices': {'sw1': {
        'commands': ['ntp authentication-key 10 md5 unique-test-password'],
        'current': ['x' * 400] * 100, 'desired': ['y' * 400] * 100,
        'notes': ['z' * 400] * 100,
    }}})
    assert details['truncated']
    assert len(json.dumps(details).encode()) <= 3500
    assert 'unique-test-password' not in json.dumps(details)
    assert '<redacted>' in details['commands'][0]


def test_post_change_findings_replace_prechange_plan(monkeypatch):
    count = 0

    def invoke(feature, device_id, mode, apply, args):
        nonlocal count
        count += 1
        args.compliance_details[feature] = {'commands': ['ntp server 192.0.2.10'] if count == 1 else []}
        return (2 if count == 1 else 0), ONE

    monkeypatch.setattr(remediation, 'invoke', invoke)
    emit = Recorder()
    remediation.run_job(None, job(['ntp']), None, emit)
    precheck = next(payload for stage, _, payload in emit.events if stage == 'precheck_complete')
    assert precheck['progress_summary']['compliance_details']['ntp']['commands']
    assert emit.events[-1][2]['progress_summary']['compliance_details']['ntp']['commands'] == []


def test_missing_archive_does_not_reuse_earlier_findings(monkeypatch, tmp_path):
    monkeypatch.setenv('NETOPS_REPORT_DIR', str(tmp_path))
    monkeypatch.setattr(remediation.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='failure'))
    args = SimpleNamespace(compliance_details={'ntp': {'commands': ['old plan']}})
    remediation.invoke('ntp', 42, 'replace', False, args)
    assert 'old plan' not in json.dumps(args.compliance_details)
    assert args.compliance_details['ntp']['notes']


def job(features=('ntp', 'syslog'), mode='add'):
    return {'id': 5, 'device_id': 42, 'hostname': '192.0.2.4', 'device': 'sw1', 'claim_token': str(uuid4()),
            'standards_snapshot': {'document': {'ntp': {'servers': ['192.0.2.10']}},
                                   'revisions': [{'standard_id': 1, 'revision': 2}]},
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
        if not apply and (feature, device_id, mode, True) in calls and outcomes.get((feature, True), (1,))[0] == 0:
            return (0, ONE)
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


@pytest.mark.parametrize('code,stage,verdict', [(0, 'completed', 'compliant'),
                                             (2, 'completed_with_warnings', 'non-compliant'),
                                             (1, 'failed', 'error')])
def test_audit_never_requests_authorization_or_applies(monkeypatch, code, stage, verdict):
    assignment = {**job(['ntp'], 'replace'), 'operation': 'audit_config'}
    assert scheduler.validate_assignment(assignment, False) == (['ntp'], 'replace')
    calls, emit = [], Recorder()
    monkeypatch.setattr(remediation, 'invoke', fake({('ntp', False): (code, ONE)}, calls))
    failed, changed, _ = remediation.run_job(None, assignment, SimpleNamespace(apply=True), emit)
    assert failed == (code == 1)
    assert changed is False
    assert all(not apply for *_, apply in calls)
    assert 'ready' not in emit.stages
    assert emit.stages[-1] == stage
    assert emit.events[-1][2]['compliance_results'] == {'ntp': verdict}


def test_command_targets_the_one_device_through_netbox_inventory():
    args = SimpleNamespace(env_file='/etc/netops/poll.env', standards=SimpleNamespace(path='/opt/standards.yaml'))
    argv = remediation.command('ntp', 42, 'replace', False, args)
    assert argv[2:] == ['ntp', '--netbox', '--netbox-filter', 'id=42', '--no-netbox-autofilter', '--replace',
                        '--fail-on-diff', '--env-file', '/etc/netops/poll.env', '--standards', '/opt/standards.yaml']
    assert remediation.command('ntp', 42, 'add', True, SimpleNamespace())[-3:] == ['--add', '--apply', '--yes']


@pytest.mark.parametrize('audit,pinned', [(False, True), (True, True), (False, False)])
def test_cluster_snapshot_and_approval_forwarding(monkeypatch, audit, pinned):
    import json
    assignment = job(['ntp'])
    assignment['profile']['allow_clearpass_cluster_changes'] = True
    if audit:
        assignment['operation'] = 'audit_config'
    cluster = [{'device_id': 42, 'address': '192.0.2.4', 'role': 'publisher'}]
    if pinned:
        assignment['standards_snapshot']['clearpass_cluster'] = cluster
    emit, calls, paths = Recorder(), [], []

    def invoke(feature, device_id, mode, apply, args):
        argv = remediation.command(feature, device_id, mode, apply, args)
        assert ('--allow-clearpass-cluster-changes' in argv) == (apply and not audit and pinned)
        assert ('--clearpass-cluster-snapshot' in argv) == pinned
        if pinned:
            paths.append(args.clearpass_cluster_snapshot)
            assert json.loads(paths[-1].read_text()) == cluster
        calls.append(apply)
        return (0 if apply or len(calls) > 1 else 2), ONE

    monkeypatch.setattr(remediation, 'invoke', invoke)
    remediation.run_job(None, assignment, SimpleNamespace(allow_clearpass_cluster_changes=True), emit)
    assert any(calls) != audit
    assert all(not path.exists() for path in paths)
    if pinned:
        assert emit.events[-1][2]['progress_summary']['clearpass_cluster'] == cluster


@pytest.mark.parametrize('value', ['false', 1, None])
def test_cluster_approval_requires_boolean(value):
    with pytest.raises(ValueError):
        remediation.validate({'features': ['ntp'], 'mode': 'add', 'allow_clearpass_cluster_changes': value})


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
    assert calls[-2:] == [('ntp', 42, 'add', True), ('ntp', 42, 'add', False)]
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


def test_missing_snapshot_never_falls_back_to_poller_file(monkeypatch):
    assignment = job(['ntp'])
    assignment.pop('standards_snapshot')
    calls = []
    monkeypatch.setattr(remediation, 'invoke', fake({}, calls))
    with pytest.raises(ValueError, match='no versioned NetBox standards'):
        remediation.run_job(None, assignment, SimpleNamespace(standards=SimpleNamespace(path='local.yaml')), Recorder())
    assert calls == []


def test_snapshot_is_used_and_temporary_file_is_removed(monkeypatch):
    import json
    paths = []

    def invoke(feature, device_id, mode, apply, args):
        paths.append(args.standards.path)
        assert json.loads(paths[-1].read_text()) == {'ntp': {'servers': ['192.0.2.10']}}
        return 0, ONE

    monkeypatch.setattr(remediation, 'invoke', invoke)
    remediation.run_job(None, job(['ntp']), SimpleNamespace(standards=SimpleNamespace(path='local.yaml')), Recorder())
    assert paths and not paths[0].exists()


@pytest.mark.parametrize('operation', ['remediate', 'audit_config'])
def test_conductor_snapshot_forwarded_to_all_phases(monkeypatch, operation):
    import json
    assignment = job(['ntp'])
    assignment['operation'] = operation
    conductor = {'device_id': 99, 'address': '192.0.2.254'}
    assignment['standards_snapshot']['mobility_conductor'] = conductor
    paths, calls, emit = [], [], Recorder()

    def invoke(feature, device_id, mode, apply, args):
        argv = remediation.command(feature, device_id, mode, apply, args)
        assert '--mobility-conductor-snapshot' in argv
        paths.append(args.mobility_conductor_snapshot)
        assert json.loads(paths[-1].read_text()) == conductor
        calls.append(apply)
        return (2 if len(calls) == 1 else 0), ONE

    monkeypatch.setattr(remediation, 'invoke', invoke)
    remediation.run_job(None, assignment, SimpleNamespace(), emit)
    assert any(calls) == (operation == 'remediate')
    assert all(not path.exists() for path in paths)
    assert emit.events[-1][2]['progress_summary']['mobility_conductor'] == conductor


def test_post_change_drift_does_not_report_compliant(monkeypatch):
    outcomes = iter([(2, ONE), (0, ONE), (2, ONE)])
    monkeypatch.setattr(remediation, 'invoke', lambda *args: next(outcomes))
    emit = Recorder()
    remediation.run_job(None, job(['ntp']), None, emit)
    assert emit.stages[-1] == 'completed_with_warnings'
    assert emit.events[-1][2]['compliance_results'] == {'ntp': 'non-compliant'}


def test_revision_acknowledgment_and_verdict_reach_the_queue_reporter(tmp_path, monkeypatch):
    from netops import archive
    from netops.upgrade.progress import Reporter

    run = archive.Run(['upgrade', '--apply'], tmp_path)
    run.args = SimpleNamespace(command='upgrade', apply=True, queue_operation='remediate')
    run.prepare()
    host = SimpleNamespace(name='sw1', hostname='192.0.2.4', platform='cisco_ios', data={'netbox_id': 42})
    events = []
    reporter = Reporter(run, event_sink=lambda event: events.append(event) or True)
    monkeypatch.setattr(remediation, 'invoke', fake({('ntp', False): (2, ONE), ('ntp', True): (0, ONE)}, []))
    remediation.run_job(None, job(['ntp']), None,
                        lambda stage, message, payload: reporter.emit(host, stage, message, payload))
    ready = next(event for event in events if event['stage'] == 'ready')
    assert ready['summary']['standards_revisions'] == [{'standard_id': 1, 'revision': 2}]
    assert events[-1]['summary']['compliance_results'] == {'ntp': 'compliant'}
