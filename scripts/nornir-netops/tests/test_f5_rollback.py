"""F5 apply/rollback round trips through the real CLI and simulated REST APIs."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from netops import cli, f5_rollback, f5_snmp, rollback
from test_run import device
from test_waf import setup as waf_setup, APP, ROOT
from test_f5_syslog import setup as syslog_setup, SYSLOG
from test_f5_banner import setup as banner_setup, SSH, GUI
from test_f5_snmp import setup as snmp_setup


@pytest.fixture
def scenario(request, tmp_path, monkeypatch):
    feature = request.param
    setup = request.getfixturevalue(feature + '_setup')
    box = setup.box
    if feature == 'snmp':
        box.responses[f5_snmp.USERS]['items'][0].update(
            securityLevel='auth-privacy', authProtocol='sha', privacyProtocol='aes', description='old user')
        box.responses[f5_snmp.COMMUNITIES]['items'][0].update(access='ro', oidSubset='.1', source='192.0.2.0/24')
        original = box.request
        def request_api(method, path, body=None):
            if method == 'POST':
                body = copy.deepcopy(body)
                identity = body['name']
                if identity.startswith('/'):
                    body['fullPath'] = identity
                    body['name'] = identity.rsplit('/', 1)[-1]
                box.writes.append((method, path, body))
                if path not in box.ignore:
                    box.responses[path]['items'].append(body)
                return {}
            return original(method, path, body)
    else:
        def request_api(method, path, body=None):
            assert method == 'PATCH'
            return box.patch_json(path, body)
    box.request = request_api
    report = tmp_path / 'forward.json'
    result = tmp_path / 'rollback-result.json'
    directory = tmp_path / 'journals'
    monkeypatch.setenv('NETOPS_ROLLBACK_DIR', str(directory))
    def apply(*flags):
        return cli.main([feature if feature != 'syslog' else 'syslog', '--no-env-file', '--csv', str(setup.csv),
                         '--standards', str(setup.standards), '--replace', '--apply', '--yes',
                         '--report', str(report), *flags])
    def undo(*flags, source=None):
        argv = ['rollback'] + ([str(source)] if source is not None else [])
        code = cli.main([*argv, '--no-env-file', '--yes', '--report', str(result), *flags])
        return code, json.loads(result.read_text()) if result.exists() else None
    def state():
        if feature == 'waf':
            return copy.deepcopy(box.app['servers'])
        if feature == 'syslog':
            return copy.deepcopy(box.responses[SYSLOG])
        if feature == 'banner':
            return copy.deepcopy({p: box.responses[p] for p in (SSH, GUI)})
        return copy.deepcopy({p: box.responses[p] for p in (f5_snmp.SNMP, f5_snmp.USERS, f5_snmp.COMMUNITIES)})
    return SimpleNamespace(setup=setup, box=box, feature=feature, report=report, result=result,
                           directory=directory, apply=apply, undo=undo, state=state, tmp_path=tmp_path)


@pytest.mark.parametrize('scenario', ['waf', 'syslog', 'banner'], indirect=True)
@pytest.mark.parametrize('source_kind', ['journal', 'archive'])
def test_round_trip_preview_restore_verify_save_and_repeat(scenario, source_kind):
    before = scenario.state()
    assert scenario.apply() == 0
    assert scenario.state() != before
    journals = list(scenario.directory.glob('*.json'))
    assert len(journals) == 1 and journals[0].stat().st_mode & 0o777 == 0o600
    source = journals[0] if source_kind == 'journal' else scenario.report
    original_archive = source.read_bytes()
    writes, saves = len(scenario.box.writes), scenario.box.saves
    code, doc = scenario.undo(source=source)
    assert code == 0
    row = doc['devices']['f5']
    assert row['action'] == 'rollback' and not row['action_from_netbox']
    assert row['configuration_scope'] == scenario.feature
    assert row['current_config']['status'] == 'observed'
    assert row['commands'] and row['implementation']['will_execute'] is False
    assert len(scenario.box.writes) == writes and scenario.box.saves == saves
    assert row['result_after']['status'] == 'not_changed'
    code, doc = scenario.undo('--apply', source=source)
    assert code == 0
    assert scenario.state() == before
    row = doc['devices']['f5']
    assert row['result_after']['status'] == 'observed'
    assert row['verified'] is True and row['saved'] is True
    assert row['backout']['complete']
    assert source.read_bytes() == original_archive
    assert all(login[4]['verify_tls'] is False for login in scenario.box.logins)
    writes, saves = len(scenario.box.writes), scenario.box.saves
    assert scenario.undo('--apply', source=source)[0] == 0
    assert len(scenario.box.writes) == writes and scenario.box.saves == saves


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
def test_latest_journal_no_save_tls_and_new_archive(scenario, monkeypatch):
    assert scenario.apply() == 0
    monkeypatch.setenv('NETOPS_F5_VERIFY_TLS', 'true')
    saves = scenario.box.saves
    code, doc = scenario.undo('--apply', '--no-save', '--f5-insecure')
    assert code == 0 and scenario.box.saves == saves
    assert scenario.box.logins[-1][4]['verify_tls'] is False
    assert doc['devices']['f5']['save_command'] is None
    assert doc['devices']['f5']['saved'] is None
    assert 'rollback_source' in doc


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
@pytest.mark.parametrize('failure', ['reject', 'ignore', 'readback', 'save'])
def test_failed_restore_retains_original_and_does_not_claim_success(scenario, failure):
    assert scenario.apply() == 0
    saves = scenario.box.saves
    if failure == 'reject': scenario.box.reject.add(SYSLOG)
    elif failure == 'ignore': scenario.box.ignore.add(SYSLOG)
    elif failure == 'save': scenario.box.save_failure = True
    else:
        original = scenario.box.request
        def fail_read(*args):
            original(*args)
            scenario.box.responses[SYSLOG] = RuntimeError('readback failed')
        scenario.box.request = fail_read
    code, doc = scenario.undo('--apply')
    assert code == 1 and scenario.box.saves == saves
    row = doc['devices']['f5']
    assert row['current_config']['status'] == 'observed'
    assert row['backout']['steps'] and row['result_after']['error']
    if failure in ('reject', 'readback'):
        assert row['result_after']['status'] == 'unknown'
    elif failure == 'ignore':
        assert row['verified'] is False and row['saved'] is False
    else:
        assert row['verified'] is True and row['saved'] is not True


@pytest.mark.parametrize('scenario', ['waf'], indirect=True)
def test_builtin_or_unknown_profile_is_never_restored(scenario):
    assert scenario.apply() == 0
    writes = len(scenario.box.writes)
    scenario.box.responses[ROOT]['items'][0]['builtIn'] = 'enabled'
    code, doc = scenario.undo('--apply')
    assert code == 1 and len(scenario.box.writes) == writes
    assert 'user-created' in doc['devices']['f5']['error']


@pytest.mark.parametrize('scenario', ['banner'], indirect=True)
def test_partial_forward_failure_has_a_reusable_journal(scenario):
    before = scenario.state()
    scenario.box.reject.add(GUI)
    assert scenario.apply() == 1
    assert list(scenario.directory.glob('*.json'))
    scenario.box.reject.clear()
    assert scenario.undo('--apply')[0] == 0
    assert scenario.state() == before


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
def test_rollback_report_itself_can_be_rolled_back(scenario):
    assert scenario.apply() == 0
    configured = scenario.state()
    assert scenario.undo('--apply')[0] == 0
    source = scenario.tmp_path / 'undo-this-rollback.json'
    source.write_bytes(scenario.result.read_bytes())
    assert scenario.undo('--apply', source=source)[0] == 0
    assert scenario.state() == configured


@pytest.mark.parametrize('scenario', ['snmp'], indirect=True)
@pytest.mark.parametrize('source_kind', ['file', 'aws'])
def test_snmp_restores_protocols_acl_users_and_communities_with_previous_secrets(scenario, source_kind, monkeypatch, capsys):
    before = scenario.state()
    assert scenario.apply() == 0
    writes = len(scenario.box.writes)
    code, doc = scenario.undo('--apply')
    assert code == 1 and len(scenario.box.writes) == writes
    assert 'supply previous' in doc['devices']['f5']['error']
    secrets = {'f5': {
        f5_snmp.USERS + '/old-resource': {'authPassword': 'old-auth"\\value', 'privacyPassword': 'old-private'},
        f5_snmp.COMMUNITIES + '/comm-public': {'communityName': 'original-community'},
    }}
    if source_kind == 'file':
        filename = scenario.tmp_path / 'old-secrets.json'
        filename.write_text(json.dumps(secrets))
        flags = ['--rollback-secrets', str(filename)]
    else:
        from netops import credentials
        def fetch(name, region):
            assert name == 'old-f5-snmp'
            return secrets
        monkeypatch.setattr(credentials, 'fetch_json_secret', fetch)
        flags = ['--rollback-secret', 'old-f5-snmp']
    code, doc = scenario.undo('--apply', *flags, source=scenario.report)
    assert code == 0
    state = scenario.state()
    assert state[f5_snmp.SNMP] == before[f5_snmp.SNMP]
    users = state[f5_snmp.USERS]['items']
    assert len(users) == 1 and users[0]['username'] == 'old-user'
    assert users[0]['authPassword'] == 'old-auth"\\value'
    assert users[0]['privacyPassword'] == 'old-private'
    assert state[f5_snmp.COMMUNITIES]['items'][0]['communityName'] == 'original-community'
    row = doc['devices']['f5']
    assert row['verified'] and row['saved']
    assert 'cannot be read back' in row['verification_note']
    output = capsys.readouterr()
    text = output.out + output.err + scenario.result.read_text()
    for fields in secrets['f5'].values():
        for secret in fields.values():
            assert secret not in text and json.dumps(secret)[1:-1] not in text


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
@pytest.mark.parametrize('change', ['endpoint', 'method', 'fields', 'traversal'])
def test_malformed_steps_fail_before_login(scenario, change):
    assert scenario.apply() == 0
    path = next(scenario.directory.glob('*.json'))
    doc = json.loads(path.read_text())
    step = doc['devices']['f5']['restore_steps'][0]
    if change == 'endpoint': step['path'] = '/mgmt/tm/sys/global-settings'
    elif change == 'method': step['method'] = 'DELETE'
    elif change == 'fields': step['body']['hostname'] = 'unexpected'
    else: step['path'] = '/mgmt/tm/sys/syslog/%2e%2e/global-settings'
    path.write_text(json.dumps(doc))
    count = len(scenario.box.logins)
    assert scenario.undo('--apply')[0] == cli.EXIT_USAGE
    assert len(scenario.box.logins) == count


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
@pytest.mark.parametrize('explicit', [True, False])
def test_report_cannot_overwrite_rollback_source(scenario, explicit):
    assert scenario.apply() == 0
    source = scenario.report if explicit else next(scenario.directory.glob('*.json'))
    original = source.read_bytes()
    argv = ['rollback'] + ([str(source)] if explicit else [])
    code = cli.main([*argv, '--no-env-file', '--apply', '--yes', '--report', str(source)])
    assert code != 0 and source.read_bytes() == original


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
def test_default_source_is_selected_before_creating_report_in_same_directory(scenario):
    assert scenario.apply() == 0
    assert cli.main(['rollback', '--no-env-file', '--apply', '--yes', '--report-dir', str(scenario.directory)]) == 0


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
def test_dry_run_archive_is_not_used_as_an_applied_run(scenario):
    assert scenario.setup.run()[0] == 0
    source = scenario.setup.csv.parent / 'report.json'
    count = len(scenario.box.logins)
    assert scenario.undo('--apply', source=source)[0] == cli.EXIT_USAGE
    assert len(scenario.box.logins) == count


@pytest.mark.parametrize('scenario', ['snmp'], indirect=True)
def test_missing_readable_original_settings_block_all_writes(scenario):
    assert scenario.apply() == 0
    path = next(scenario.directory.glob('*.json'))
    doc = json.loads(path.read_text())
    doc['devices']['f5']['unsupported'].append('Some previous SNMP scalar values were not returned by BIG-IP.')
    path.write_text(json.dumps(doc))
    writes = len(scenario.box.writes)
    code, report = scenario.undo('--apply')
    assert code == 1 and len(scenario.box.writes) == writes
    assert 'scalar values' in report['devices']['f5']['error']
    assert not report['devices']['f5']['implementation']['will_execute']


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
def test_mixed_archive_dispatches_ssh_and_rest_and_supports_limit(scenario, request):
    device = request.getfixturevalue('device')
    device['devices']['sw1'].lines.append('logging host 192.0.2.88')
    assert scenario.apply() == 0
    doc = json.loads(scenario.report.read_text())
    doc['devices']['sw1'] = {'hostname': '10.1.1.1', 'platform': 'cisco_ios', 'applied': True,
                            'rollback': ['no logging host 192.0.2.88'], 'commands': ['logging host 192.0.2.88']}
    scenario.report.write_text(json.dumps(doc))
    assert scenario.undo('--apply', '--limit', 'f5', source=scenario.report)[0] == 0
    assert not device['config']
    code, report = scenario.undo('--apply', source=scenario.report)
    assert code == 0 and set(report['devices']) == {'f5', 'sw1'}
    assert device['config']['sw1'] == ['no logging host 192.0.2.88']


@pytest.mark.parametrize('scenario', ['snmp'], indirect=True)
def test_partitioned_original_identity_survives_older_archives(scenario):
    old_user = scenario.box.responses[f5_snmp.USERS]['items'][0]
    old_user.update(partition='Common', fullPath='/Common/old-resource')
    community = scenario.box.responses[f5_snmp.COMMUNITIES]['items'][0]
    community.update(partition='Common', fullPath='/Common/comm-public')
    assert scenario.apply() == 0
    doc = json.loads(scenario.report.read_text())
    for step in doc['devices']['f5']['backout']['steps']:
        step.pop('target', None)  # Version-2 archives originally lacked this field.
    scenario.report.write_text(json.dumps(doc))
    old = {'f5': {
        f5_snmp.USERS + '/~Common~old-resource': {'authPassword': 'old-auth', 'privacyPassword': 'old-priv'},
        f5_snmp.COMMUNITIES + '/~Common~comm-public': {'communityName': 'old-community'},
    }}
    filename = scenario.tmp_path / 'old.json'; filename.write_text(json.dumps(old))
    code, report = scenario.undo('--apply', '--rollback-secrets', str(filename), source=scenario.report)
    assert code == 0
    assert scenario.box.responses[f5_snmp.USERS]['items'][0]['fullPath'] == '/Common/old-resource'
    assert scenario.box.responses[f5_snmp.COMMUNITIES]['items'][0]['fullPath'] == '/Common/comm-public'


@pytest.mark.parametrize('scenario', ['syslog'], indirect=True)
def test_interrupted_rest_rollback_retains_checkpoint(scenario):
    assert scenario.apply() == 0
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    scenario.box.request = interrupted
    code, report = scenario.undo('--apply')
    assert code == 130 and report['status'] == 'interrupted'
    row = report['devices']['f5']
    assert row['current_config']['status'] == 'observed'
    assert row['backout']['complete'] and row['result_after']['status'] == 'unknown'


@pytest.mark.parametrize('scenario', ['snmp'], indirect=True)
def test_snmp_restores_modified_user_in_place_and_reapplies_secrets(scenario):
    user = scenario.box.responses[f5_snmp.USERS]['items'][0]
    user.update(username='nmsuser', access='rw', description='keep original description')
    assert scenario.apply() == 0
    filename = scenario.tmp_path / 'previous.json'
    filename.write_text(json.dumps({'f5': {
        f5_snmp.USERS + '/old-resource': {'authPassword': 'old-auth', 'privacyPassword': 'old-priv'},
        f5_snmp.COMMUNITIES + '/comm-public': {'communityName': 'old-community'},
    }}))
    flags = ['--apply', '--rollback-secrets', str(filename)]
    code, doc = scenario.undo(*flags)
    assert code == 0
    restored = scenario.box.responses[f5_snmp.USERS]['items'][0]
    assert restored['name'] == 'old-resource' and restored['access'] == 'rw'
    assert restored['description'] == 'keep original description'
    assert restored['authPassword'] == 'old-auth'
    assert any(method == 'PATCH' and path.endswith('/old-resource') for method, path, body in scenario.box.writes)
    assert scenario.undo(*flags)[0] == 0
    assert len(scenario.box.responses[f5_snmp.USERS]['items']) == 1
    assert len(scenario.box.responses[f5_snmp.COMMUNITIES]['items']) == 1


@pytest.mark.parametrize('scenario', ['snmp'], indirect=True)
def test_duplicate_live_snmp_identity_blocks_restore(scenario):
    assert scenario.apply() == 0
    users = scenario.box.responses[f5_snmp.USERS]['items']
    users.append(copy.deepcopy(users[0]))
    writes = len(scenario.box.writes)
    code, doc = scenario.undo('--apply')
    assert code == 1 and len(scenario.box.writes) == writes
    assert 'duplicate SNMP' in doc['devices']['f5']['error']
