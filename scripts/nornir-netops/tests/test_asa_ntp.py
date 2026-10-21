"""ASA address migrations retain nameif sources and authentication bindings."""
import copy
import json
from types import SimpleNamespace

import pytest

from netops import cli, ntp_asa
from netops.core import MODE_ADD, MODE_REPLACE, render
from netops.features.ntp import FEATURE, plan_ntp
from netops.rollback import default_reversal
from test_ntp import parse_args
from test_run import FakeDevice, device, login


def plan(lines, *, source=None, vrf=None, data=None, mode=MODE_REPLACE, servers=None, auth=None):
    document = {'ntp': {'servers': servers or ['192.0.2.10', '192.0.2.11']}}
    if source:
        document['ntp']['source'] = source
    if vrf:
        document['ntp']['vrf'] = vrf
    if auth:
        document['ntp']['authentication'] = auth
    args = parse_args(document=document)
    args.ntp_platforms = ['cisco_asa']
    desired = FEATURE.build_desired(args)
    host = SimpleNamespace(platform='cisco_asa', data=data or {})
    keys, variables = FEATURE.per_device(desired.keys, copy.deepcopy(desired.variables), host)
    current = ntp_asa.parse('\n'.join(lines))
    context = {'platform': 'cisco_asa', 'variables': variables, 'notes': []}
    add, remove = plan_ntp(current, keys, mode, context)
    commands = render('ntp', 'cisco_asa', add, remove, variables)
    return commands, default_reversal(commands, current, remove), keys, context


@pytest.mark.parametrize('source', ['inside', 'management', 'dmz_prod', 'Management0', None])
@pytest.mark.parametrize('mode', [MODE_ADD, MODE_REPLACE])
def test_source_inheritance_apply_repeat_and_rollback(source, mode):
    suffix = ' source ' + source if source else ''
    old = 'ntp server 192.0.2.9 key 7' + suffix
    original = ['ntp authenticate', 'ntp trusted-key 7', 'ntp authentication-key 7 md5 hidden 8', old]
    commands, reversal, _, _ = plan(original, mode=mode)
    additions = ['ntp server ' + ip + ' key 7' + suffix for ip in ['192.0.2.10', '192.0.2.11']]
    assert commands == additions + (['no ' + old] if mode == MODE_REPLACE else [])
    box = FakeDevice(original)
    box.apply(commands)
    assert plan(box.lines, mode=mode)[0] == []
    box.apply(reversal.commands)
    assert set(box.lines) == set(original)


def test_mixed_unspecified_and_defined_sources_reuse_defined_source():
    old = ['ntp server 192.0.2.8', 'ntp server 192.0.2.9 source inside']
    assert plan(old)[0][:2] == ['ntp server 192.0.2.10 source inside', 'ntp server 192.0.2.11 source inside']
    old[0] = 'ntp server 192.0.2.10'
    assert plan(old)[0] == ['ntp server 192.0.2.11 source inside', 'no ' + old[1]]


def test_conflicting_sources_block_new_addresses_unless_explicitly_selected():
    old = ['ntp server 192.0.2.8 source inside', 'ntp server 192.0.2.9 source outside']
    with pytest.raises(ValueError, match='sources differ'):
        plan(old)
    assert plan(old, source='management')[0][:2] == [
        'ntp server 192.0.2.10 source management', 'ntp server 192.0.2.11 source management']


@pytest.mark.parametrize('source', ['Gi0/1', 'GigabitEthernet0/1', 'inside'])
def test_tagged_interface_is_translated_to_nameif(source):
    original = ['interface GigabitEthernet0/1', ' nameif inside', ' security-level 100',
                '!', 'ntp server 192.0.2.9 source outside']
    commands = plan(original, data={'source_interface': {'ntp': source}, 'ntp_vrf': 'default'})[0]
    assert commands[:2] == ['ntp server 192.0.2.10 source inside', 'ntp server 192.0.2.11 source inside']
    assert all('nameif' not in command and 'interface ' not in command for command in commands)


def test_unmapped_physical_source_does_not_become_invalid_asa_command():
    with pytest.raises(ValueError, match='nameif mapping'):
        plan([], data={'source_interface': {'ntp': 'Gi0/9'}})


@pytest.mark.parametrize('data,vrf', [({}, 'MGMT'), ({'ntp_vrf': 'MGMT'}, None)])
def test_named_vrf_selection_fails(data, vrf):
    with pytest.raises(ValueError, match='does not support named VRFs'):
        plan(['ntp server 192.0.2.9 source inside'], vrf=vrf, data=data)


def test_default_vrf_is_valid_and_no_vrf_syntax_is_emitted():
    assert plan([], vrf='default')[0] == ['ntp server 192.0.2.10', 'ntp server 192.0.2.11']


@pytest.mark.parametrize('line', [
    'ntp server', 'ntp server vrf MGMT 192.0.2.9', 'ntp server 192.0.2.9 source',
    'ntp server 192.0.2.9 source-address 198.51.100.1', 'ntp server 192.0.2.9 iburst',
    'ntp server 192.0.2.9 use-vrf MGMT', 'ntp server time.example.net'])
def test_invalid_or_unsupported_current_context_blocks_changes(line):
    with pytest.raises(ValueError):
        plan([line])


def test_ipv6_and_unconfigured_devices():
    assert plan([], servers=['2001:db8::10'])[0] == ['ntp server 2001:db8::10']
    with pytest.raises(ValueError, match='IPv4 or IPv6'):
        plan([], servers=['time.example.net'])


def test_verification_retains_source_even_if_readback_loses_it():
    _, _, keys, context = plan(['ntp server 192.0.2.9 source inside'])
    after = ntp_asa.parse('ntp server 192.0.2.10\nntp server 192.0.2.11')
    add, remove = plan_ntp(after, keys, MODE_REPLACE, {**context, 'verification': True})
    assert len(add) == len(remove) == 2
    assert 'ntp server 192.0.2.10 source inside' in render('ntp', 'cisco_asa', add, remove, context['variables'])


def test_authentication_management_does_not_remove_interface_metadata(monkeypatch):
    monkeypatch.setenv('NETOPS_NTP_KEY_1', 'test-key-material')
    original = ['interface GigabitEthernet0/1', ' nameif inside', 'ntp server 192.0.2.9 source inside']
    commands = plan(original, auth={'key_id': 1, 'type': 'sha256'})[0]
    assert commands[:2] == ['ntp authentication-key 1 sha256 test-key-material', 'ntp trusted-key 1']
    assert 'ntp server 192.0.2.10 key 1 source inside' in commands
    assert not any('interface ' in command or 'nameif' in command for command in commands)


@pytest.mark.parametrize('lose_source', [False, True])
def test_cli_audit_apply_verify_and_repeat(device, login, tmp_path, lose_source):
    csv = tmp_path / 'asa.csv'
    csv.write_text('host,name,platform,region\n192.0.2.1,asa,cisco_asa,us-east\n')
    standards = tmp_path / 'standards.yaml'
    standards.write_text('ntp:\n  regions:\n    us-east:\n      servers: [192.0.2.10, 192.0.2.11]\n')
    report = tmp_path / 'report.json'
    original = ['ntp server 192.0.2.9 source inside']

    class ASA(FakeDevice):
        def show(self, command):
            if command == 'show running-config interface':
                return 'interface GigabitEthernet0/1\n nameif inside'
            assert command == 'show running-config ntp'
            return '\n'.join(self.lines)

        def apply(self, commands):
            super().apply(commands)
            if lose_source:
                self.lines = [line.replace(' source inside', '') for line in self.lines]

    device['devices']['asa'] = ASA(original)

    def invoke(*flags):
        code = cli.main(['ntp', '--no-env-file', '--csv', str(csv), '--standards', str(standards),
                         '--replace', '--report', str(report), *flags])
        return code, json.loads(report.read_text())['devices']['asa']

    code, row = invoke('--fail-on-diff')
    assert code == cli.EXIT_DIFF and not device['config']
    expected = ['ntp server 192.0.2.10 source inside', 'ntp server 192.0.2.11 source inside',
                'no ntp server 192.0.2.9 source inside']
    assert row['commands'] == expected
    code, row = invoke('--apply', '-y')
    assert device['config']['asa'] == expected
    if lose_source:
        assert code == cli.EXIT_FAILED and row['verified'] is False
        assert 'write memory' not in device['commands']['asa']
    else:
        assert code == cli.EXIT_OK and row['verified'] and row['saved']
        assert 'write memory' in device['commands']['asa']
        assert invoke('--apply', '-y')[1]['compliant']
        assert device['config']['asa'] == expected


def test_nameif_case_difference_is_not_silently_treated_as_compliant():
    old = ['ntp server 192.0.2.10 source Inside']
    commands = plan(old, source='inside', servers=['192.0.2.10'])[0]
    assert commands == ['no ' + old[0], 'ntp server 192.0.2.10 source inside']


def test_different_key_bindings_require_explicit_authentication_for_new_addresses():
    with pytest.raises(ValueError, match='authentication keys differ'):
        plan(['ntp server 192.0.2.8 key 1 source inside', 'ntp server 192.0.2.9 key 2 source inside'])
