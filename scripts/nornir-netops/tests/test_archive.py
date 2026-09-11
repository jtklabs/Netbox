"""Archive evidence through the real CLI, planners and simulated devices."""
import json
import os
from pathlib import Path

import pytest

from netops import archive, cli, runner
from test_run import device, csv_file, login, run
from test_waf import setup as waf_setup, APP, ROOT
from test_f5_syslog import setup as syslog_setup, SYSLOG, RETAINED, EXTRA
from test_f5_banner import setup as banner_setup, SSH, GUI
from test_f5_snmp import setup as snmp_setup
from test_syslog_netbox import setup as switches


def latest():
    paths = sorted(Path(os.environ['NETOPS_REPORT_DIR']).glob('*.json'))
    return json.loads(paths[-1].read_text())


def test_unique_private_archives_without_report_flag(device, csv_file, login):
    for _ in range(2):
        assert run(csv_file, '-s', '10.99.99.1', '--replace') == 0
    paths = list(Path(os.environ['NETOPS_REPORT_DIR']).glob('*.json'))
    assert len(paths) == 2
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in paths)
    docs = [json.loads(p.read_text()) for p in paths]
    assert docs[0]['run_id'] != docs[1]['run_id']
    for doc in docs:
        assert doc['schema_version'] == 2
        assert doc['started_at'] <= doc['finished_at']
        assert doc['dry_run'] and doc['exit_code'] == 0
        for name, row in doc['devices'].items():
            assert row['action'] == 'manage' and not row['action_from_netbox']
            assert row['action_source'] == 'cli'
            before = row['current_config']
            assert before['status'] == 'observed'
            assert before['config'][0]['line'].startswith('ntp server 10.10.10.1')
            steps = row['implementation']['steps']
            assert any(s.get('command', '').startswith('no ntp server') for s in steps)
            assert not row['implementation']['will_execute']
            reversal = row['backout']
            assert reversal['complete']
            assert any(s.get('command') == 'no ntp server 10.99.99.1' + (' iburst' if name == 'leaf1' else '') for s in reversal['steps'])
            assert reversal['steps'][-1]['purpose'] == 'persist_restored_config'
            assert row['result_after']['status'] == 'not_changed'
            assert row['result_after']['config'] == before['config']
    assert not device['config']


@pytest.mark.parametrize('verify', [True, False])
def test_apply_archive_records_observed_result_or_unknown(device, csv_file, login, verify):
    flags = ['--apply', '--yes', '--no-rollback-file'] + ([] if verify else ['--no-verify'])
    assert run(csv_file, '-s', '10.99.99.1', *flags) == 0
    for row in latest()['devices'].values():
        result = row['result_after']
        assert result['applied'] and result['saved']
        assert result['status'] == ('observed' if verify else 'unknown')
        assert (result['config'] is not None) is verify
        if verify:
            assert any('10.99.99.1' in e['line'] for e in result['config'])


@pytest.mark.parametrize('tags,flags,expected,source', [
    (['syslog-add', 'syslog-manage'], [], ['add', 'manage'], True),
    (['syslog-add', 'syslog-audit'], ['--policy', 'netbox'], ['add', 'audit'], True),
    (['syslog-manage', 'syslog-audit'], ['--add'], ['add', 'add'], False),
    (['syslog-add', 'syslog-audit'], ['--policy', 'manage'], ['manage', 'manage'], False),
])
def test_actions_resolve_per_device_and_explicit_override(switches, tags, flags, expected, source):
    for device, tag in zip(switches.nb.devices.values(), tags):
        device['tags'] = [{'slug': tag}]
    code, report = switches.run(*flags)
    assert code == 0
    for name, action in zip(('ios', 'eos'), expected):
        row = report['devices'][name]
        assert row['action'] == action
        assert row['action_from_netbox'] is source
        assert row['action_source'] == ('netbox' if source else 'cli')
        assert (row['netbox_policy_tag'] is not None) is source
    assert report['action'] == ('mixed' if len(set(expected)) > 1 else expected[0])


def test_conflicting_netbox_policy_does_not_invent_action(switches):
    switches.nb.devices[7]['tags'] = [{'slug': 'syslog-add'}, {'slug': 'syslog-manage'}]
    code, report = switches.run()
    assert code == 1
    row = report['devices']['ios']
    assert row['action'] is None and row['action_from_netbox']
    assert row['current_config']['status'] == 'unavailable'
    assert not row['backout']['complete']


def test_failed_device_keeps_original_and_plan(switches):
    switches.boxes['ios'].reject = True
    code, report = switches.run('--apply')
    assert code == 1
    row = report['devices']['ios']
    assert row['current_config']['status'] == 'observed'
    assert row['implementation']['steps'] and row['backout']['steps']
    assert row['result_after']['status'] == 'unknown'
    assert row['result_after']['error']
    assert report['devices']['eos']['result_after']['status'] == 'observed'


def test_plan_checkpoint_exists_before_ssh_write(device, csv_file, login, monkeypatch):
    original = runner.netmiko_send_config
    seen = []
    def send(task, **kwargs):
        row = latest()['devices'][task.host.name]
        assert row['status'] == 'applying'
        assert row['current_config']['status'] == 'observed'
        assert row['backout']['steps']
        assert row['result_after']['status'] == 'unknown'
        seen.append(task.host.name)
        return original(task, **kwargs)
    monkeypatch.setattr(runner, 'netmiko_send_config', send)
    assert run(csv_file, '-s', '10.99.99.1', '--apply', '--yes', '--no-rollback-file') == 0
    assert set(seen) == {'sw1', 'leaf1'}


def test_waf_rest_reversal_restores_original_objects(waf_setup):
    before = json.loads(json.dumps(waf_setup.box.app['servers']))
    code, report = waf_setup.run('--policy', 'manage', '--apply')
    assert code == 0
    row = report['devices']['f5']
    assert row['current_config']['config'] == [{'path': APP, 'servers': before}]
    assert row['result_after']['status'] == 'observed'
    for step in row['backout']['steps']:
        if step['purpose'] == 'restore':
            waf_setup.box.patch_json(step['path'], step['body'])
    assert waf_setup.box.app['servers'] == before
    assert row['backout']['complete']


def test_waf_discovery_failure_is_unavailable(waf_setup):
    waf_setup.box.responses[ROOT] = RuntimeError('could not read profiles')
    code, report = waf_setup.run()
    assert code == 1
    row = report['devices']['f5']
    assert row['current_config']['status'] == 'unavailable'
    assert row['result_after']['config'] is None
    assert not row['backout']['complete']


def test_system_syslog_rest_reversal_can_be_replayed(syslog_setup):
    code, report = syslog_setup.run('--policy', 'manage', '--apply')
    assert code == 0
    row = report['devices']['f5']
    assert row['backout']['complete']
    for step in row['backout']['steps']:
        if step['purpose'] == 'restore':
            syslog_setup.box.patch_json(step['path'], step['body'])
    assert syslog_setup.box.responses[SYSLOG]['remoteServers'] == [RETAINED, EXTRA]


def test_banner_rest_reversal_contains_both_original_bodies(banner_setup):
    code, report = banner_setup.run('--apply')
    assert code == 0
    row = report['devices']['f5']
    assert row['result_after']['status'] == 'observed'
    for step in row['backout']['steps']:
        if step['purpose'] == 'restore':
            banner_setup.box.patch_json(step['path'], step['body'])
    assert banner_setup.box.responses[SSH]['bannerText'] == 'Old SSH notice'
    assert banner_setup.box.responses[GUI]['guiSecurityBannerText'] == 'Old GUI notice'
    assert row['backout']['complete']


def test_snmp_reversal_marks_unreadable_previous_secrets(snmp_setup):
    code, report = snmp_setup.run('--replace')
    assert code == 0
    row = report['devices']['f5']
    assert not row['backout']['complete']
    steps = row['backout']['steps']
    assert any(s.get('method') == 'DELETE' and '/users/' in s['path'] for s in steps)
    assert any('communityName' in s.get('requires_secret_fields', []) for s in steps)
    assert any(s.get('body', {}).get('snmpv2c') == 'enabled' for s in steps if s.get('body'))
    assert 'old-community-password' not in json.dumps(report)
    assert 'encrypted-old-auth' not in json.dumps(report)


def test_json_escaped_credentials_are_redacted_recursively():
    secret = 'quoted"and\\backslash'
    archive.protect([secret])
    value = {'commands': ['POST /path ' + json.dumps({'authPassword': secret, 'nested': {'password': secret}})],
             'config': {'token': 'token-value', 'secret_value': secret}}
    cleaned = json.dumps(archive.clean(value))
    assert 'backslash' not in cleaned and 'token-value' not in cleaned


@pytest.mark.parametrize('args', [['selftest'], ['ntp', '--unknown-flag'], ['ntp', '--csv', '/does/not/exist']])
def test_utility_and_early_error_runs_produce_archives(args):
    try:
        code = cli.main(args)
    except SystemExit as exc:
        code = exc.code
    doc = latest()
    assert doc['exit_code'] == code
    assert doc['finished_at'] and doc['schema_version'] == 2
    if code:
        assert doc['status'] == 'failed' and doc['error']


def test_report_directory_env_file_and_explicit_file_override(tmp_path):
    folder = tmp_path / 'archives'
    env = tmp_path / '.env'
    env.write_text(f'NETOPS_REPORT_DIR={folder}\n')
    os.environ.pop('NETOPS_REPORT_DIR')
    assert cli.main(['selftest']) == 0
    assert len(list(folder.glob('*.json'))) == 1
    explicit = tmp_path / 'chosen.json'
    assert cli.main(['selftest', '--report', str(explicit)]) == 0
    assert explicit.exists() and len(list(folder.glob('*.json'))) == 1


def test_unwritable_archive_prevents_device_access(tmp_path, device, csv_file, login):
    not_directory = tmp_path / 'file'
    not_directory.write_text('existing')
    assert run(csv_file, '-s', '10.99.99.1', '--apply', '--yes', '--report-dir', str(not_directory)) == 1
    assert not device['config'] and not device['commands']


def test_interrupted_run_finishes_archive(monkeypatch):
    monkeypatch.setattr(cli, '_run', lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.main(['selftest']) == 130
    assert latest()['status'] == 'interrupted'


def test_archive_switch_reversal_restores_source_and_destination(switches):
    before = {name: sorted(box.lines) for name, box in switches.boxes.items()}
    code, report = switches.run('--policy', 'manage', '--apply')
    assert code == 0
    for name, row in report['devices'].items():
        assert row['backout']['complete']
        switches.boxes[name].apply([s['command'] for s in row['backout']['steps'] if s['purpose'] == 'restore'])
        assert sorted(switches.boxes[name].lines) == before[name]


def test_audit_policy_and_untagged_fallback_are_recorded(switches):
    for item in switches.nb.devices.values():
        item['tags'] = []
    code, report = switches.run('--apply')
    assert code == 0
    for row in report['devices'].values():
        assert row['action'] == 'audit' and row['action_from_netbox']
        assert row['netbox_policy_tag'] is None
        assert not row['implementation']['will_execute']
        assert row['result_after']['status'] == 'not_changed'


def test_f5_checkpoint_precedes_rest_mutation(syslog_setup):
    patch = syslog_setup.box.patch_json
    seen = []
    def tracked(path, body):
        doc = json.loads((syslog_setup.csv.parent / 'report.json').read_text())
        row = doc['devices']['f5']
        assert row['current_config']['status'] == 'observed'
        assert row['result_after']['status'] == 'unknown'
        assert row['backout']['complete']
        seen.append(path)
        return patch(path, body)
    syslog_setup.box.patch_json = tracked
    code, report = syslog_setup.run('--apply')
    assert code == 0 and seen == [SYSLOG]


def test_rollback_archive_has_observations_and_reapply_plan(device, csv_file, login, tmp_path, monkeypatch):
    journal_dir = tmp_path / 'journals'
    monkeypatch.setenv('NETOPS_ROLLBACK_DIR', str(journal_dir))
    assert run(csv_file, '-s', '10.99.99.1', '--apply', '--yes', '--limit', 'sw1') == 0
    assert cli.main(['rollback', '--no-env-file', '--apply', '--yes']) == 0
    row = latest()['devices']['sw1']
    assert row['action'] == 'rollback'
    assert row['configuration_scope'] == 'ntp'
    assert any('10.99.99.1' in e['line'] for e in row['current_config']['config'])
    assert not any('10.99.99.1' in e['line'] for e in row['result_after']['config'])
    assert not row['backout']['complete']
    assert any(s['command'] == 'ntp server 10.99.99.1' for s in row['backout']['steps'] if s['purpose'] == 'restore')


def test_discovery_archive_contains_inventory_platforms(device, csv_file, login):
    assert cli.main(['discover', '--no-env-file', '--csv', csv_file]) == 0
    for row in latest()['devices'].values():
        assert row['action'] == 'audit'
        assert row['current_config']['config']['platform'] in ('cisco_ios', 'arista_eos')


def test_interrupted_apply_keeps_prewrite_evidence(device, csv_file, login, monkeypatch):
    def interrupted(task, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(runner, 'netmiko_send_config', interrupted)
    assert run(csv_file, '-s', '10.99.99.1', '--apply', '--yes', '--limit', 'sw1') == 130
    doc = latest()
    assert doc['status'] == 'interrupted'
    row = doc['devices']['sw1']
    assert row['current_config']['status'] == 'observed'
    assert row['backout']['complete']
    assert row['result_after']['status'] == 'unknown'


@pytest.mark.parametrize('platform,header,end', [('cisco_ios', 'banner login ^C', '^C'), ('arista_eos', 'banner login', 'EOF')])
def test_banner_removal_plan_includes_original_body_for_restore(platform, header, end):
    from netops.features.banner import parse_banners, reverse
    current = parse_banners(f'{header}\nOriginal login notice\n{end}\n')
    reversal = reverse(['no banner login'], current, current,
                       {'platform': platform, 'variables': {'delimiter': None}, 'added': []})
    assert not reversal.unsupported
    assert 'Original login notice' in '\n'.join(reversal.commands)
    parsed = parse_banners('\n'.join(reversal.commands))
    assert parsed[0].data['body'] == current[0].data['body']
