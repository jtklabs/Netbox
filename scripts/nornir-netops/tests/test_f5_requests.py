"""Request-log completion gate: old logs, transient API failures and restoration."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from netops.errors import DeviceHTTPError
from netops.upgrade import f5, f5_requests, f5_workflow
from test_upgrade_f5 import FakeBigIP, iso, options, profile, run_unit  # noqa: F401
from test_f5_drain import DrainBigIP

CUTOFF = "2026-10-20T10:00:00+00:00"
NEW = {"id": "123456", "requestDatetime": "2026-10-20T10:00:01Z"}
OLD = {"id": "old", "requestDatetime": "2026-10-19T23:59:59Z"}
ACTIVE = {"waf": {"provisioned": True, "policy_count": 1}, "facts": {"failover_state": "active"}}


@pytest.fixture
def clock(monkeypatch):
    timer = SimpleNamespace(now=0)
    def sleep(seconds):
        assert seconds > 0
        timer.now += seconds
    monkeypatch.setattr(f5_requests, 'time', SimpleNamespace(monotonic=lambda: timer.now, sleep=sleep))
    return timer


def wait(client, after=ACTIVE, timeout=3, plan=None):
    plan = plan if plan is not None else {"waf_requests": {"after": CUTOFF}}
    emit = Mock()
    options = SimpleNamespace(validation_timeout=timeout, poll_interval=1)
    f5_requests.wait_for_request(client, after, plan, options, emit)
    return plan, emit


def test_one_new_request_passes_after_empty_and_old_pages(clock):
    client = Mock(timeout=30)
    client.request.side_effect = [{"items": []}, {"items": [OLD]}, {"items": [NEW]}]
    plan, _ = wait(client)
    assert client.request.call_count == 3
    assert plan['waf_requests']['status'] == 'passed'
    assert plan['waf_requests']['request_id'] == NEW['id']
    assert client.request.call_args.kwargs['timeout'] == 1


@pytest.mark.parametrize('row', [OLD, {**NEW, 'requestDatetime': CUTOFF},
                                 {**NEW, 'requestDatetime': '2026-10-20T10:00:01'},
                                 {**NEW, 'requestDatetime': 'not a date'}])
def test_stale_equal_and_unparseable_dates_never_complete(clock, row):
    client = Mock(timeout=30)
    client.request.return_value = {'items': [row]}
    plan = {'waf_requests': {'after': CUTOFF}}
    with pytest.raises(TimeoutError, match='No post-upgrade WAF request log'):
        wait(client, plan=plan)
    assert plan['waf_requests']['status'] == 'timed_out'
    assert clock.now == 3


@pytest.mark.parametrize('response', [{}, {'items': None}, {'items': ['invalid']}])
def test_malformed_api_response_is_retried(clock, response):
    client = Mock(timeout=30)
    client.request.side_effect = [response, {'items': [NEW]}]
    assert wait(client)[0]['waf_requests']['status'] == 'passed'
    assert client.request.call_count == 2


@pytest.mark.parametrize('error', [DeviceHTTPError('F5', 'GET', 'events', 401),
                                 DeviceHTTPError('F5', 'GET', 'events', 503),
                                 requests.ReadTimeout('sensitive request content')])
def test_transient_errors_retry_until_a_new_event(clock, error):
    client = Mock(timeout=30)
    client.request.side_effect = [error, {'items': [NEW]}]
    plan, emit = wait(client)
    assert plan['waf_requests']['status'] == 'passed'
    assert 'sensitive request content' not in str(emit.call_args_list)
    assert 'last_error' not in plan['waf_requests']


def test_profile_timeout_gets_its_own_full_window(clock):
    client = Mock(timeout=30)
    client.request.return_value = {'items': []}
    plan = {'approved_profile': {'validation_timeout': 4800}, 'waf_requests': {'after': CUTOFF}}
    options = SimpleNamespace(validation_timeout=1, poll_interval=600)
    with pytest.raises(TimeoutError, match='4800s'):
        f5_requests.wait_for_request(client, ACTIVE, plan, options, Mock())
    assert clock.now == 4800
    assert plan['waf_requests']['attempts'] == 8


def test_response_after_deadline_does_not_complete(clock):
    client = Mock(timeout=30)
    def late(*args, **kwargs):
        clock.now = 4
        return {'items': [NEW]}
    client.request.side_effect = late
    with pytest.raises(TimeoutError):
        wait(client)


@pytest.mark.parametrize('after,reason', [
    ({'facts': {'failover_state': 'active'}}, 'not provisioned'),
    ({**ACTIVE, 'waf': {'provisioned': True, 'policy_count': 0}}, 'no policies'),
    ({**ACTIVE, 'facts': {'failover_state': 'standby'}}, 'standby'),
])
def test_skips_do_not_query_log_or_clock(clock, after, reason):
    client = Mock(timeout=30)
    plan, _ = wait(client, after)
    assert plan['waf_requests']['status'] == 'skipped'
    assert reason in plan['waf_requests']['reason']
    client.request.assert_not_called()


def test_unknown_policy_count_is_not_empty(clock):
    with pytest.raises(ValueError, match='unreadable'):
        wait(Mock(timeout=30), {'waf': {'provisioned': True, 'policy_count': None}})


def test_clock_read_failure_is_deferred_then_retried(clock):
    client = Mock(timeout=30)
    client.request.side_effect = [requests.ReadTimeout(), requests.ReadTimeout(),
                                 {'commandResult': '2026-10-20T10:00:00Z\n'}, {'items': [NEW]}]
    plan = {}
    f5_requests.mark_reconnected(client, ACTIVE, plan)
    assert 'after' not in plan['waf_requests']
    wait(client, plan=plan)
    assert plan['waf_requests']['after'] == CUTOFF
    assert plan['waf_requests']['status'] == 'passed'


def test_timezone_offsets_compare_as_instants(clock):
    client = Mock(timeout=30)
    client.request.return_value = {'items': [{**NEW, 'requestDatetime': '2026-10-20T06:00:01-04:00'}]}
    assert wait(client)[0]['waf_requests']['status'] == 'passed'


class WafBigIP(DrainBigIP):
    def __init__(self):
        super().__init__()
        self.read_count = lambda: 0
        self.log_reads = 0
        self.request_items = [NEW]

    def _get(self, path):
        if path == '/mgmt/tm/sys/provision':
            return {'items': [{'name': 'ltm', 'level': 'nominal'}, {'name': 'asm', 'level': 'nominal'}]}
        if path == f5.ASM_POLICIES:
            # Parent-only policies still count; don't inspect enforcement fields.
            return {'items': [{'fullPath': '/Common/parent'}]}
        if path == f5_requests.REQUESTS:
            assert self.rebooted_to == 'HD1.2'
            assert self.addresses['/Common/192.0.2.10']
            assert self.saved_addresses['/Common/192.0.2.10']
            self.log_reads += 1
            return {'items': self.request_items}
        return super()._get(path)

    def _post(self, path, payload):
        command = payload.get('utilCmdArgs', '')
        if 'ASMReady' in command:
            return {'commandResult': '1'}
        if f5_requests.CLOCK in command:
            assert self.rebooted_to == 'HD1.2'
            return {'commandResult': '2026-10-20T10:00:00Z'}
        if path == '/mgmt/tm/sys' and payload.get('command') == 'reboot':
            self.addresses = dict(self.saved_addresses)
        return FakeBigIP._post(self, path, payload)


@pytest.fixture
def unit(monkeypatch):
    unit = WafBigIP()
    monkeypatch.setattr(f5_workflow.f5_waf, 'Client', unit)
    return unit


@pytest.mark.parametrize('drain', [False, True])
def test_final_gate_after_validation_and_traffic_restore(profile, options, unit, drain):
    options.apply = True
    result, reporter, stages = run_unit(replace(profile, drain_connections=drain), options)
    assert not result.failed, result.result
    assert stages.index('waf_requests') > max(i for i, stage in enumerate(stages) if stage == 'validating')
    if drain:
        assert stages.index('waf_requests') > stages.index('restoring_virtuals')
    assert stages[-1] == 'completed'
    final = reporter.emit.call_args.args[3]['progress_summary']
    assert final['phase']['step'] == final['phase']['total'] == 9
    assert final['waf_requests']['status'] == 'passed'
    assert unit.log_reads == 1


def test_no_new_log_needs_recovery_but_preserves_restored_traffic(profile, options, unit, clock):
    options.apply = True
    unit.request_items = [OLD]
    result, reporter, stages = run_unit(replace(profile, drain_connections=True), options)
    assert result.failed and stages[-1] == 'recovery_required'
    assert not any(stage.startswith('completed') for stage in stages)
    assert unit.addresses['/Common/192.0.2.10'] and unit.saved_addresses['/Common/192.0.2.10']
    assert not unit.addresses['/Common/192.0.2.20']
    assert reporter.emit.call_args.args[3]['progress_summary']['waf_requests']['status'] == 'timed_out'


def test_health_failure_does_not_start_log_check(profile, options, unit, monkeypatch):
    options.apply = True
    options.validation_timeout = 0
    collect = f5.collect
    def broken(client, progress):
        snapshot = collect(client, progress)
        if unit.rebooted_to:
            snapshot['errors']['waf_ready'] = 'ASM still loading'
        return snapshot
    monkeypatch.setattr(f5, 'collect', broken)
    result, _, stages = run_unit(replace(profile, drain_connections=True), options)
    assert result.failed and 'waf_requests' not in stages
    assert unit.log_reads == 0 and not any(unit.addresses.values())


def test_standby_with_policies_finishes_without_log_reads(profile, options, unit):
    options.apply = True
    unit.failover = 'standby'
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed and stages[-1].startswith('completed')
    assert unit.log_reads == 0
    assert reporter.emit.call_args.args[3]['progress_summary']['waf_requests']['reason'] == 'Unit is on standby'


def test_persistent_401_uses_the_whole_deadline(clock):
    client = Mock(timeout=30)
    client.request.side_effect = DeviceHTTPError('F5', 'GET', 'events', 401)
    plan = {'waf_requests': {'after': CUTOFF}}
    with pytest.raises(TimeoutError):
        wait(client, plan=plan)
    assert client.request.call_count == 3 and clock.now == 3
    assert plan['waf_requests']['last_error'] == 'HTTP 401'


def test_raw_request_data_is_not_retained(clock):
    client = Mock(timeout=30)
    client.request.return_value = {'items': [{**NEW, 'rawRequest': 'private-content', 'clientIp': 'private-ip'}]}
    plan, emit = wait(client)
    assert 'private-content' not in str(plan) + str(emit.call_args_list)
    assert 'private-ip' not in str(plan) + str(emit.call_args_list)


@pytest.mark.parametrize('mode', ['dry_run', 'stage_only', 'already_current'])
def test_non_upgrades_do_not_query_request_logs(profile, options, unit, mode):
    options.apply = mode != 'dry_run'
    options.stage_only = mode == 'stage_only'
    if mode == 'already_current':
        unit.version = profile.target_version
    result, _, stages = run_unit(profile, options)
    assert not result.failed, result.result
    assert 'waf_requests' not in stages and unit.log_reads == 0


def test_cancel_during_final_log_wait_preserves_restored_traffic(profile, options, unit):
    from netops import cancellation
    options.apply = True
    options.validation_timeout = 4800
    control = cancellation.Control()
    original = unit._get
    def get(path):
        result = original(path)
        if path == f5_requests.REQUESTS:
            control.requested.set()
            return {'items': [OLD]}
        return result
    unit._get = get
    with cancellation.bind(control), pytest.raises(cancellation.Cancelled):
        run_unit(replace(profile, drain_connections=True), options)
    assert unit.log_reads == 1
    assert unit.addresses['/Common/192.0.2.10'] and unit.saved_addresses['/Common/192.0.2.10']
    assert not unit.addresses['/Common/192.0.2.20']
