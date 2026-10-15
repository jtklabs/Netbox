"""Regression tests for rejected reboots and reused/stale installation volumes."""
from dataclasses import replace
from unittest.mock import Mock

import pytest
import requests

from netops.f5_waf import Client
from netops.upgrade import f5, f5_workflow
from test_upgrade_f5 import options, profile, iso, unit, run_unit


def test_reboot_sends_volume_as_top_level_property():
    client = Mock(timeout=30)
    client.request.return_value = {}
    assert f5.reboot_to(client, 'HD1.2') is True
    client.request.assert_called_once_with('POST', '/mgmt/tm/sys',
        {'command': 'reboot', 'volume': 'HD1.2'}, timeout=60)


@pytest.mark.parametrize('status', [400, 401, 403, 500])
def test_real_client_http_rejections_are_not_reboot_disconnects(status):
    client = Client(Mock(hostname='192.0.2.10', username='admin', password='unused'))
    client.session = Mock()
    client.session.request.return_value = Mock(status_code=status, text='rejected')
    with pytest.raises(RuntimeError):
        f5.reboot_to(client, 'HD1.2')
    client.session.request.assert_called_once()


@pytest.mark.parametrize('error', [requests.ConnectionError('reset'), requests.ReadTimeout('timeout')])
def test_transport_loss_is_ambiguous_and_never_retried(error):
    client = Mock(timeout=30)
    client.request.side_effect = error
    assert f5.reboot_to(client, 'HD1.2') is False
    client.request.assert_called_once()


def test_rejected_reboot_ends_workflow_without_reconnect_wait(profile, options, unit, monkeypatch):
    options.apply = True
    original = unit._post
    def post(path, payload):
        if path == '/mgmt/tm/sys':
            raise RuntimeError('F5 POST /mgmt/tm/sys failed (400): invalid parameter')
        return original(path, payload)
    unit._post = post
    wait = Mock(side_effect=AssertionError('must not wait for rejected reboot'))
    monkeypatch.setattr(f5_workflow, 'wait_for_target', wait)
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and stages[-1] == 'recovery_required'
    assert '400' in reporter.emit.call_args.args[2]
    wait.assert_not_called()


def test_reused_target_skips_noop_install_but_copies_config_before_reboot(profile, options, unit):
    options.apply = True
    unit.volumes[1].update(version=profile.target_version, build='0.0.19')
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed
    assert not any(p == '/mgmt/tm/sys/software/image' and m == 'POST' for m,p,_ in unit.calls)
    copy = next(i for i,(_,p,data) in enumerate(unit.calls)
                if p == '/mgmt/tm/util/bash' and '/sbin/cpcfg' in (data or {}).get('utilCmdArgs', ''))
    reboot = next(i for i,(m,p,_) in enumerate(unit.calls) if m == 'POST' and p == '/mgmt/tm/sys')
    assert copy < reboot
    assert '--source=HD1.1 HD1.2' in unit.calls[copy][2]['utilCmdArgs']


def test_f5_omits_active_property_for_inactive_volumes(profile, unit):
    unit.volumes[1].update(version=profile.target_version, build='0.0.19')
    del unit.volumes[1]['active']
    assert f5_workflow.installed_target(unit.volumes[1], profile)
    f5.copy_configuration(unit, 'HD1.1', 'HD1.2', 30)
    assert any('/sbin/cpcfg' in (data or {}).get('utilCmdArgs','') for _,_,data in unit.calls)


@pytest.mark.parametrize('output', ['copy failed\nNETOPS_CPCFG_EXIT=1\n', '', 'some output',
                                   'NETOPS_CPCFG_EXIT=0\nmore unexpected output'])
def test_unconfirmed_copy_prevents_drain_and_reboot(profile, options, unit, output):
    options.apply = True
    original = unit._post
    def post(path, payload):
        if 'NETOPS_CPCFG_RUNNING' in payload.get('utilCmdArgs', ''):
            return {'commandResult': output}
        return original(path, payload)
    unit._post = post
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and unit.rebooted_to is None
    assert 'reconnecting' not in stages and 'draining' not in stages
    assert 'configuration copy' in reporter.emit.call_args.args[2]


@pytest.mark.parametrize('source,target', [('HD1.1', 'HD1.1'), ('HD1.1; reboot', 'HD1.2'), ('HD1.2','HD1.1')])
def test_copy_cannot_target_active_or_unsafe_volumes(unit, source, target):
    with pytest.raises(ValueError):
        f5.copy_configuration(unit, source, target, 30)
    assert not any(m == 'POST' for m,_,_ in unit.calls)


@pytest.mark.parametrize('change', [{'version':'16.1.4'}, {'build':'0.0.18'}, {'status':'installing 0 pct'}, {'active':True}, {}])
def test_stale_complete_and_missing_volume_wait_for_target(profile, options, monkeypatch, change):
    target = dict(name='HD1.2', version=profile.target_version, build='0.0.19', status='complete', active=False)
    first = dict(target, **change) if change else {}
    if change.get('active'):
        with pytest.raises(ValueError, match='became active'):
            monkeypatch.setattr(f5, 'volume_info', lambda *a: first)
            f5_workflow.wait_for_install(None,'HD1.2', options, Mock(), profile)
        return
    responses = Mock(side_effect=[first, target])
    monkeypatch.setattr(f5,'volume_info', responses)
    f5_workflow.wait_for_install(None, 'HD1.2', options, Mock(), profile)
    assert responses.call_count == 2


def test_persistently_stale_install_times_out_without_reboot(profile, options, unit, monkeypatch):
    options.apply = True
    options.install_timeout = 0
    original = unit._post
    def post(path, payload):
        if path == '/mgmt/tm/sys/software/image':
            return {}  # accepted request but installation never starts
        return original(path, payload)
    unit._post = post
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and unit.rebooted_to is None
    assert 'target version/build' in reporter.emit.call_args.args[2]


def test_http_success_with_install_command_error_fails(unit):
    unit.request = Mock(return_value={'commandResult': 'install refused'})
    with pytest.raises(RuntimeError, match='install refused'):
        f5.install(unit,'image.iso','HD1.2',False)


def test_reconnect_progress_shows_observed_old_volume(profile, options, unit):
    options.reload_timeout = 0.01
    emit = Mock()
    with pytest.raises(TimeoutError):
        f5_workflow.wait_for_target(Mock(), {}, profile, 'HD1.2', options, emit)
    messages = [c.args[1] for c in emit.call_args_list]
    assert any('HD1.1 on 17.1.1.3' in message for message in messages)


def test_copy_waits_over_an_hour_without_relaunch(unit, monkeypatch):
    clock = [0]
    monkeypatch.setattr(f5.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(f5.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    original = unit._post
    launches = []
    def post(path, payload):
        command = payload.get('utilCmdArgs','')
        if '/sbin/cpcfg' in command:
            launches.append(command)
            assert 'nohup' in command and 'flock -n' in command and 'umask 077' in command
        if 'NETOPS_CPCFG_RUNNING' in command and clock[0] < 3630:
            return {'commandResult':'NETOPS_CPCFG_RUNNING\n'}
        return original(path, payload)
    unit._post = post
    result = f5.copy_configuration(unit,'HD1.1','HD1.2',7200,poll=30)
    assert clock[0] == 3630 and len(launches) == 1
    assert result['verified'] and result['log_path'].startswith('/var/tmp/netops-cpcfg-')


def test_copy_deadline_leaves_ambiguous_operation_for_recovery(unit, monkeypatch):
    clock = [0]
    monkeypatch.setattr(f5.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(f5.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    original = unit._post
    def post(path,payload):
        if 'NETOPS_CPCFG_RUNNING' in payload.get('utilCmdArgs',''):
            return {'commandResult':'NETOPS_CPCFG_RUNNING'}
        return original(path,payload)
    unit._post=post
    with pytest.raises(TimeoutError,match='may still be running, reboot cancelled'):
        f5.copy_configuration(unit,'HD1.1','HD1.2',45,poll=30)
    assert clock[0] == 45
    launches=[data for _,_,data in unit.calls if '/sbin/cpcfg' in (data or {}).get('utilCmdArgs','')]
    assert len(launches)==1


def test_copy_unacknowledged_launch_is_not_retried(unit):
    original=unit._post
    def post(path,payload):
        if '/sbin/cpcfg' in payload.get('utilCmdArgs',''):
            return {'commandResult':'unexpected output'}
        return original(path,payload)
    unit._post=post
    with pytest.raises(RuntimeError,match='launch was not confirmed'):
        f5.copy_configuration(unit,'HD1.1','HD1.2',30)
    assert len([c for c in unit.calls if c[0]=='POST'])==1


def test_large_image_checksum_gets_time_to_complete():
    client = Mock(timeout=30)
    digest = '0fc6d3549c3439f47ce00c1828edda96'
    def request(method, path, payload, timeout=None):
        if timeout is None or timeout < 90:
            raise requests.ReadTimeout('cold-cache checksum exceeded ordinary REST timeout')
        return {'commandResult': digest + '  /shared/images/image.iso\n'}
    client.request.side_effect = request
    assert f5.remote_md5(client, 'image.iso') == digest


def unavailable_license(unit):
    original = unit._get
    def get(path):
        if path == '/mgmt/tm/sys/license':
            return {'apiRawValues':{'apiAnonymous':"Can't load license, may not be operational\n"}}
        return original(path)
    unit._get=get


def test_preexisting_unavailable_license_finishes_with_explicit_warning(profile, options, unit):
    unavailable_license(unit)
    options.apply=True
    result,reporter,stages=run_unit(replace(profile,license_check_date=''),options)
    assert not result.failed and stages[-1]=='completed_with_warnings'
    assert any('License unavailable before' in c.args[2] for c in reporter.emit.call_args_list)
    assert any('license was unavailable before' in str(c.args[3]) for c in reporter.emit.call_args_list)


def test_required_license_date_still_blocks_unlicensed_unit(profile, options, unit):
    unavailable_license(unit)
    options.apply=True
    result,reporter,stages=run_unit(profile,options)
    assert result.failed and stages[-1]=='blocked' and unit.rebooted_to is None


def test_newly_lost_license_remains_error(profile, options, unit):
    before=f5.collect(unit,lambda _:None)
    plan=f5_workflow.preflight(before,replace(profile,license_check_date=''))
    unavailable_license(unit)
    after=f5.collect(unit,lambda _:None)
    findings=f5_workflow.target_findings(after,replace(profile,license_check_date=''),plan)
    assert any(f['check']=='license' and f['severity']=='error' for f in findings)


def test_unknown_license_response_is_not_treated_as_known_lab_condition(profile, unit):
    original=unit._get
    def get(path):
        if path=='/mgmt/tm/sys/license':return {'apiRawValues':{'apiAnonymous':'unexpected failure'}}
        return original(path)
    unit._get=get
    snapshot=f5.collect(unit,lambda _:None)
    assert snapshot['facts']['license_unavailable'] is False
    plan=f5_workflow.preflight(snapshot,replace(profile,license_check_date=''))
    assert any('license response could not be interpreted' in b for b in plan['blockers'])
    findings=f5_workflow.target_findings(snapshot,replace(profile,license_check_date=''),plan)
    assert any(f['check']=='license' and f['severity']=='error' for f in findings)
