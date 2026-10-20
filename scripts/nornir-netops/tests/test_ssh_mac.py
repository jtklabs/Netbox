"""Exercise complete MAC lists, CLI execution and post-change verification."""
import argparse
import json
from types import SimpleNamespace

import pytest
from nornir.core.task import Result

from netops import cli, runner
from netops.core import render
from netops.features import ssh_mac as mac
from netops.standards import Standards
from netops.upgrade import remediation


def desired(settings=None, flags=()):
    parser = argparse.ArgumentParser()
    mac.add_arguments(parser)
    args = parser.parse_args(flags)
    args.standards = Standards(document={'ssh_mac': settings or {}})
    return mac.build_desired(args)


def context(platform='cisco_ios', scope='both'):
    return dict(platform=platform, variables=desired({'ios_scope': scope}).variables, advisories=[])


@pytest.mark.parametrize('platform,output', [
    ('cisco_ios', 'ip ssh client algorithm mac hmac-sha2-512\nip ssh server algorithm mac hmac-sha2-512'),
    ('arista_eos', 'management ssh\n   mac hmac-sha2-512\n!\nmanagement api http-commands\n   mac hmac-sha1'),
    ('cisco_asa', 'ssh cipher integrity high\nssh cipher encryption high'),
])
def test_compliant_lists_are_idempotent(platform, output):
    entries = mac.FEATURE.platforms[platform].parse(output)
    for mode in ('add', 'replace'):
        ctx = context(platform)
        assert mac.plan(entries, [], mode, ctx) == ([], [])
        assert not ctx['advisories']


@pytest.mark.parametrize('platform,output', [
    ('cisco_ios', 'ip ssh server algorithm mac hmac-sha2-512 hmac-sha1'),
    ('arista_eos', 'management ssh\n   mac hmac-sha2-512 hmac-sha1'),
    ('cisco_asa', 'ssh cipher integrity custom hmac-sha1'),
])
def test_weak_extras_fail_and_add_mode_never_narrows_lists(platform, output):
    entries = mac.FEATURE.platforms[platform].parse(output)
    ctx = context(platform)
    assert mac.plan(entries, [], 'add', ctx) == ([], [])
    assert ctx['advisories']
    ctx = context(platform)
    additions, removals = mac.plan(entries, [], 'replace', ctx)
    assert additions and not removals
    commands = render('ssh-mac', platform, additions, removals, ctx['variables'])
    assert not any(c.startswith('no ') for c in commands)


def test_ios_client_only_does_not_touch_server():
    ctx = context(scope='client')
    entries = mac.parse_ios('ip ssh client algorithm mac hmac-sha2-512\nip ssh server algorithm mac hmac-sha1')
    assert mac.plan(entries, [], 'replace', ctx) == ([], [])
    assert desired({'ios_scope': 'both'}, ['--ios-scope', 'client']).variables['policies']['cisco_ios']['ios_scope'] == 'client'


@pytest.mark.parametrize('settings', [
    {'macs': []}, {'macs': 'hmac-sha2-512'}, {'macs': [None]},
    {'macs': ['hmac-sha2-512', 'hmac-sha2-512']}, {'ios_scope': 'both\nreload'},
    {'ios_scope': []}, {'asa_integrity': 'invalid'}, {'cipher': 'aes256-ctr'},
])
def test_invalid_policy_rejected(settings):
    with pytest.raises(ValueError):
        desired(settings)


def test_eos_rollback_preserves_management_ssh_block():
    entries = mac.parse_eos('management ssh\n   mac hmac-sha1\n   idle-timeout 30')
    reversal = mac.reverse([], entries, [], {**context('arista_eos'), 'added': ['server:' + mac.MAC]})
    assert reversal.commands == ['management ssh', 'mac hmac-sha1', 'exit']


def test_worker_translates_profile_name_to_cli():
    assert remediation.validate({'features': ['ssh_mac'], 'mode': 'replace'}) == (['ssh_mac'], 'replace')
    assert remediation.command('ssh_mac', 1, 'replace', False, SimpleNamespace())[2] == 'ssh-mac'

@pytest.mark.parametrize('platform,initial', [
    ('cisco_ios', 'ip ssh server algorithm mac hmac-sha1'),
    ('arista_eos', 'management ssh\n   mac hmac-sha1'),
    ('cisco_asa', 'ssh cipher integrity medium'),
])
@pytest.mark.parametrize('configured', [False, True])
def test_cli_dry_run_apply_verify_and_repeat(tmp_path, monkeypatch, platform, initial, configured):
    state = {'output': initial, 'writes': [], 'saves': []}
    monkeypatch.setenv('NET_USER', 'operator')
    monkeypatch.setenv('NET_PASS', 'test-password')
    csv = tmp_path / 'devices.csv'
    csv.write_text(f'host,name,platform\n192.0.2.1,device,{platform}\n')
    report = tmp_path / 'report.json'
    standards = tmp_path / 'standards.yaml'
    standards.write_text('ssh_mac:\n  macs: [hmac-sha2-256, hmac-sha2-512]\n  platforms:\n'
                         '    arista_eos: {macs: [hmac-sha2-256]}\n'
                         '    cisco_asa: {macs: [hmac-sha2-256]}\n')
    standard_flags = ['--standards', str(standards)] if configured else []

    def read(task, command_string, **kwargs):
        if command_string.startswith('show '):
            return Result(host=task.host, result=state['output'])
        state['saves'].append(command_string)
        return Result(host=task.host, result='[OK]')

    def push(task, config_commands, **kwargs):
        state['writes'].append(config_commands)
        state['output'] = ('management ssh\n   ' + config_commands[1] if platform == 'arista_eos'
                           else '\n'.join(config_commands))
        return Result(host=task.host, result='\n'.join(config_commands))

    monkeypatch.setattr(runner, 'netmiko_send_command', read)
    monkeypatch.setattr(runner, 'netmiko_send_config', push)

    def run(*flags):
        code = cli.main(['ssh-mac', '--no-env-file', '--csv', str(csv), '--report', str(report), *standard_flags, *flags])
        return code, json.loads(report.read_text())['devices']['device']

    assert run('--add', '--apply', '--yes', '--fail-on-diff')[0] == cli.EXIT_DIFF
    assert not state['writes']
    code, row = run('--replace', '--fail-on-diff')
    assert code == cli.EXIT_DIFF and row['commands']
    assert not state['writes']
    code, row = run('--replace', '--apply', '--yes')
    assert code == cli.EXIT_OK
    assert row['verified'] and row['saved']
    if configured:
        expected = {'cisco_ios': 'ip ssh server algorithm mac hmac-sha2-256 hmac-sha2-512',
                    'arista_eos': 'mac hmac-sha2-256',
                    'cisco_asa': 'ssh cipher integrity custom hmac-sha2-256'}
        assert expected[platform] in state['writes'][0]
    assert state['saves'] == ['write memory']
    assert run('--replace', '--apply', '--yes')[1]['compliant']
    assert len(state['writes']) == 1

    # The command was accepted, but a weaker algorithm remains after the push.
    state['output'] = initial
    monkeypatch.setattr(runner, 'netmiko_send_config', lambda task, **kwargs: Result(host=task.host, result='OK'))
    code, row = run('--replace', '--apply', '--yes')
    assert code == cli.EXIT_FAILED and row['verified'] is False
    assert row['saved'] is False and state['saves'] == ['write memory']


@pytest.mark.parametrize('scope', ['client', 'server'])
def test_ios_rollback_restores_default_when_no_explicit_list(scope):
    reversal = mac.reverse([], [], [], {**context(), 'added': [scope + ':' + mac.MAC]})
    assert reversal.commands == [f'default ip ssh {scope} algorithm mac']


@pytest.mark.parametrize('platform,settings,expected', [
    ('cisco_ios', {'macs': ['hmac-sha2-256', 'hmac-sha2-512']}, [
        'ip ssh client algorithm mac hmac-sha2-256 hmac-sha2-512',
        'ip ssh server algorithm mac hmac-sha2-256 hmac-sha2-512']),
    ('arista_eos', {'platforms': {'arista_eos': {'macs': ['hmac-sha2-256-etm@openssh.com']}}}, [
        'management ssh', 'mac hmac-sha2-256-etm@openssh.com', 'exit']),
    ('cisco_asa', {'platforms': {'cisco_asa': {'macs': ['hmac-sha2-256', 'hmac-sha1']}}}, [
        'ssh cipher integrity custom hmac-sha2-256:hmac-sha1']),
    ('cisco_asa', {'asa_integrity': 'fips'}, ['ssh cipher integrity fips']),
])
def test_yaml_drives_exact_commands(platform, settings, expected):
    variables = desired(settings).variables
    ctx = {'platform': platform, 'variables': variables}
    add, remove = mac.plan([], [], 'replace', ctx)
    assert render('ssh-mac', platform, add, remove, variables) == expected


def test_platform_override_precedence_and_order_independent_compliance():
    settings = {'macs': ['hmac-sha2-512'], 'ios_scope': 'both', 'platforms': {
        'cisco_ios': {'macs': ['hmac-sha2-256', 'hmac-sha2-512'], 'ios_scope': 'server'},
        'arista_eos': {'macs': ['hmac-sha2-256']},
    }}
    variables = desired(settings).variables
    assert mac.wanted_for('cisco_ios', variables) == ['server:hmac-sha2-256,hmac-sha2-512']
    assert mac.wanted_for('arista_eos', variables) == ['server:hmac-sha2-256']
    assert mac.wanted_for('juniper_junos', variables) == ['server:hmac-sha2-512']
    assert mac.wanted_for('cisco_asa', variables) == ['integrity:high']
    assert mac.policy_for('cisco_ios', desired(settings, ['--ios-scope', 'client']).variables)['ios_scope'] == 'client'
    entries = mac.parse_ios('ip ssh server algorithm mac hmac-sha2-512 hmac-sha2-256')
    assert mac.plan(entries, [], 'replace', {'platform': 'cisco_ios', 'variables': variables}) == ([], [])


@pytest.mark.parametrize('settings', [
    {'macs': ['hmac-sha2-512\nreload']}, {'macs': ['hmac-sha2-256;reload']},
    {'macs': ['hmac-sha2-256 hmac-sha1']}, {'macs': ['hmac-sha2-256:hmac-sha1']},
    {'macs': ['hmac-sha2-256'] * 33}, {'macs': [False]},
    {'platforms': {}}, {'platforms': []}, {'platforms': {'ios': {'macs': ['hmac-sha2-256']}}},
    {'platforms': {'arista_eos': {}}}, {'platforms': {'arista_eos': {'ios_scope': 'both'}}},
    {'platforms': {'cisco_asa': {'macs': ['hmac-sha2-256'], 'asa_integrity': 'high'}}},
    {'platforms': {'juniper_junos': {'macs': []}}},
])
def test_nested_or_unsafe_policy_is_rejected(settings):
    with pytest.raises(ValueError):
        desired(settings)


@pytest.mark.parametrize('line', [
    'ssh cipher integrity custom hmac-sha1:hmac-sha2-256',
    'ssh cipher integrity custom "hmac-sha2-256:hmac-sha1"',
])
def test_asa_custom_lists_parse_and_compare_without_order_drift(line):
    settings = {'platforms': {'cisco_asa': {'macs': ['hmac-sha2-256', 'hmac-sha1']}}}
    ctx = {'platform': 'cisco_asa', 'variables': desired(settings).variables}
    assert mac.plan(mac.parse_asa(line), [], 'replace', ctx) == ([], [])


def test_validation_contract_matches_netbox_package():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    assert (root / 'scripts/nornir-netops/netops/ssh_mac_policy.py').read_text() == (
        root / 'plugins/netbox-compliance/netbox_compliance/ssh_mac_policy.py').read_text()
