"""Manual F5 gates must restore traffic, preserve evidence and remain job scoped."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops import cancellation
from netops.upgrade import checks, f5, f5_requests, f5_validation, report, workflow
from test_upgrade_f5 import FakeBigIP, stats
from test_f5_requests import iso, options, profile, unit  # noqa: F401


def override(step):
    return {'step': step, 'user_id': 1, 'username': 'operator',
            'timestamp': '2026-10-20T15:00:00Z', 'reason': 'Verified device and accepted difference'}


def control():
    result = cancellation.Control()
    result.observe({'supports_validation_overrides': True})
    return result


@pytest.mark.parametrize('timed_out', [False, True])
@pytest.mark.parametrize('restore_error', [None, 'request', 'state'])
def test_manual_health_then_request_gate(profile, options, unit, monkeypatch, timed_out, restore_error):
    options.apply = True
    options.validation_timeout = 0 if timed_out else 1
    unit.request_items = []
    initial = dict(unit.virtuals)
    channel = control()
    collect = f5.collect
    def observe(client, progress):
        result = collect(client, progress)
        if unit.rebooted_to:
            result['errors']['nodes'] = 'Static node differs'
        return result
    monkeypatch.setattr(f5, 'collect', observe)
    # Avoid real waits; cancellation still has priority at each checkpoint.
    monkeypatch.setattr(cancellation, 'sleep', lambda *a, **k: cancellation.checkpoint())
    monkeypatch.setattr(workflow, 'comparison_report', lambda *a: ({}, None))
    if restore_error:
        def fail(name, payload):
            if payload.get('enabled'):
                if restore_error == 'request':
                    raise RuntimeError('restore request failed')
                unit.virtuals[name] = False  # A successful PATCH did not actually restore the state.
        unit.patch_failure = fail
    events = []
    def emit(host, stage, message, payload, **kwargs):
        events.append((stage, message, payload))
        summary = payload['progress_summary']
        if stage == 'validating' and (summary.get('awaiting_manual_validation') if timed_out
                                      else 'Comparison' in message):
            assert not any(unit.virtuals.values())
            channel.observe({'supports_validation_overrides': True, 'validation_overrides': {'8': override(8)}})
        if stage == 'waf_requests' and summary.get('awaiting_manual_validation'):
            assert unit.virtuals == initial
            assert unit.saved_virtuals == initial
            assert '9' not in channel.validation_overrides  # Step 8 did not authorize step 9.
            channel.observe({'supports_validation_overrides': True,
                             'validation_overrides': {'8': override(8), '9': override(9)}})
        return True
    host = SimpleNamespace(name='bigip-a', hostname='192.0.2.10', platform='f5_tmsh',
                           username='admin', password='secret', data={})
    reporter = Mock(emit=Mock(side_effect=emit))
    with cancellation.bind(channel):
        result = workflow.upgrade_device(SimpleNamespace(host=host),
                                         replace(profile, drain_connections=True), options, reporter)
    if restore_error:
        assert result.failed
        assert not any(unit.virtuals.values())
        assert not any(stage == 'waf_requests' for stage, _, _ in events)
        return
    assert not result.failed and result.result['status'] == 'completed_with_warnings'
    assert any(f.get('manually_overridden') and f['original_severity'] == 'error' for f in result.result['findings'])
    final = events[-1][2]
    assert final['upgrade_plan']['validation_overrides'] == {'8': override(8), '9': override(9)}
    assert final['upgrade_plan']['waf_requests']['status'] == 'manually_passed'
    assert final['progress_summary']['manual_validation_step'] is None
    assert not final['progress_summary']['awaiting_manual_validation']
    assert unit.virtuals == initial


def test_manual_wait_is_cancellable_and_separate_from_other_jobs():
    first, second = control(), control()
    first.observe({'supports_validation_overrides': True, 'validation_overrides': {'8': override(8)}})
    with cancellation.bind(second):
        assert not f5_validation.forced({}, 8, Mock())
    with cancellation.bind(first):
        assert not f5_validation.forced({}, 9, Mock())
        first.observe({'cancel_requested': True})
        with pytest.raises(cancellation.Cancelled):
            f5_validation.forced({}, 8, Mock(), timed_out=True)


def test_cancel_while_waiting_for_manual_action():
    channel = control()
    def emit(*args):
        channel.observe({'cancel_requested': True})
    with cancellation.bind(channel), pytest.raises(cancellation.Cancelled):
        f5_validation.forced({}, 9, emit, timed_out=True)


def test_old_server_or_unscheduled_run_keeps_timeout_behavior():
    with cancellation.bind(cancellation.Control()):
        assert not f5_validation.forced({}, 8, Mock(), timed_out=True)
    assert not f5_validation.forced({}, 9, Mock(), timed_out=True)


def test_older_heartbeat_cannot_erase_override_and_ack_is_not_repeated():
    channel = control()
    channel.observe({'supports_validation_overrides': True, 'validation_overrides': {'8': override(8)}})
    channel.observe({'supports_validation_overrides': True, 'validation_overrides': {}})
    plan, emit = {}, Mock()
    with cancellation.bind(channel):
        assert f5_validation.forced(plan, 8, emit)
        assert f5_validation.forced(plan, 8, emit)
    assert plan['validation_overrides']['8'] == override(8)
    assert emit.call_count == 1


def test_unreadable_policy_count_still_allows_separate_manual_log_gate():
    channel = control()
    channel.observe({'supports_validation_overrides': True, 'validation_overrides': {'9': override(9)}})
    plan = {}
    with cancellation.bind(channel):
        f5_requests.wait_for_request(Mock(), {'waf': {'provisioned': True, 'policy_count': None}},
                                    plan, SimpleNamespace(validation_timeout=10), Mock())
    assert plan['waf_requests']['status'] == 'manually_passed'


def test_dynamic_nodes_excluded_but_static_node_drift_still_fails():
    client = FakeBigIP()
    original = client.get_json
    rows = [{'name': '/Common/static', 'availability': 'available', 'enabled': 'enabled'},
            {'name': '/Common/app_auto_10.0.0.1', 'availability': 'offline', 'enabled': 'enabled'}]
    path, mapping, _ = f5.STATS['nodes']
    client.get_json = lambda value: stats(rows, mapping) if value == path else original(value)
    before = f5.collect(client, lambda _: None)
    rows[1:] = [{'name': '/Common/other_auto_10.0.0.2', 'availability': 'available', 'enabled': 'disabled'},
                {'name': '/Common/third_auto_10.0.0.3', 'availability': 'available', 'enabled': 'enabled'}]
    after = f5.collect(client, lambda _: None)
    assert before['metrics']['counts']['nodes'] == after['metrics']['counts']['nodes'] == 1
    assert not checks.compare(before, after)
    assert len(after['raw'][path]['entries']) == 3  # Raw diagnostic evidence is preserved.
    rows[0]['availability'] = 'offline'
    assert any(f['severity'] == 'error' for f in checks.compare(before, f5.collect(client, lambda _: None)))


def test_comparison_report_identifies_manual_pass():
    snapshot = f5.collect(FakeBigIP(), lambda _: None)
    text = report.build(SimpleNamespace(name='unit', hostname='192.0.2.10'), snapshot, snapshot, [],
                        {'validation_overrides': {'9': override(9)}}, 'completed_with_warnings')
    assert 'Step 9: operator at 2026-10-20T15:00:00Z' in text
    assert 'Verified device and accepted difference' in text
