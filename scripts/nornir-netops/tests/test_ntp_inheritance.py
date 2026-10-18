"""Address migrations reuse device transport when inventory intent is absent."""
import copy
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from netops import cli, junos_ntp
from netops.core import MODE_ADD, MODE_REPLACE, render
from netops.features.ntp import FEATURE, plan_ntp
from netops.rollback import default_reversal
from test_ntp import parse_args
from test_ntp_reconciliation import EosDevice
from test_run import FakeDevice, device, login, netbox, run_netbox
from test_junos_ntp import setup
from test_waf import setup as waf_setup


def plan(platform, lines, *flags, data=None, mode=MODE_REPLACE):
    wanted = FEATURE.build_desired(parse_args(['-s', '192.0.2.10,192.0.2.11', *flags]))
    host = SimpleNamespace(data=data if data is not None else {'source_interface': {}, 'ntp_vrf': ''})
    keys, variables = FEATURE.per_device(wanted.keys, copy.deepcopy(wanted.variables), host)
    current = FEATURE.platforms[platform].parse('\n'.join(lines))
    context = {'platform': platform, 'variables': variables, 'notes': []}
    add, remove = plan_ntp(current, keys, mode, context)
    commands = render('ntp', platform, add, remove, variables)
    return commands, default_reversal(commands, current, remove), keys, context


@pytest.mark.parametrize('platform,global_lines,old,new', [
    ('cisco_ios', [], 'ntp server vrf MGMT 192.0.2.9 source GigabitEthernet0/0',
     'ntp server vrf MGMT {ip} source GigabitEthernet0/0'),
    ('cisco_ios', ['ntp source Loopback0'], 'ntp server vrf MGMT 192.0.2.9',
     'ntp server vrf MGMT {ip}'),
    ('cisco_ios', [], 'ntp server 192.0.2.9 source Loopback0 version 4',
     'ntp server {ip} source Loopback0 version 4'),
    ('arista_eos', ['ntp local-interface vrf MGMT Management1'], 'ntp server vrf MGMT 192.0.2.9 iburst',
     'ntp server vrf MGMT {ip} iburst'),
    ('arista_eos', [], 'ntp server vrf MGMT 192.0.2.9 iburst source Management1',
     'ntp server vrf MGMT {ip} iburst source Management1'),
    ('arista_eos', [], 'ntp server vrf MGMT 192.0.2.9 iburst source-address 198.51.100.55',
     'ntp server vrf MGMT {ip} iburst source-address 198.51.100.55'),
    ('cisco_nxos', ['ntp source-interface mgmt0'], 'ntp server 192.0.2.9 use-vrf management',
     'ntp server {ip} use-vrf management'),
    ('cisco_nxos', ['ntp source 198.51.100.55'], 'ntp server 192.0.2.9 use-vrf management',
     'ntp server {ip} use-vrf management'),
])
@pytest.mark.parametrize('mode', [MODE_ADD, MODE_REPLACE])
def test_reuses_device_context_applies_idempotently_and_rolls_back(platform, global_lines, old, new, mode):
    original = [*global_lines, old]
    commands, reversal, _, _ = plan(platform, original, mode=mode)
    expected = [new.format(ip=ip) for ip in ('192.0.2.10', '192.0.2.11')]
    assert commands == expected + (['no ' + old] if mode == MODE_REPLACE else [])
    box = EosDevice(original) if platform == 'arista_eos' else FakeDevice(original)
    box.apply(commands)
    assert set(box.lines) == set(global_lines + expected + ([old] if mode == MODE_ADD else []))
    assert plan(platform, box.lines, mode=mode)[0] == []
    box.apply(reversal.commands)
    assert set(box.lines) == set(original)


@pytest.mark.parametrize('platform,old,new', [
    ('cisco_ios', ['ntp server vrf A 192.0.2.9 source Lo0', 'ntp server vrf B 192.0.2.9 source Lo1'],
     ['ntp server vrf {vrf} {ip} source Loopback{source}']),
    ('cisco_nxos', ['ntp server 192.0.2.9 use-vrf A', 'ntp server 192.0.2.9 use-vrf B'],
     ['ntp server {ip} use-vrf {vrf}']),
])
def test_multiple_existing_routing_tables_each_get_the_new_servers(platform, old, new):
    commands, reversal, _, _ = plan(platform, old)
    additions = [new[0].format(ip=ip, vrf=vrf, source=source)
                 for ip in ('192.0.2.10', '192.0.2.11') for vrf, source in (('A', 0), ('B', 1))]
    assert commands == additions + ['no ' + line for line in old]
    box = FakeDevice(old)
    box.apply(commands)
    assert plan(platform, box.lines)[0] == []
    box.apply(reversal.commands)
    assert set(box.lines) == set(old)


def test_explicit_default_vrf_and_source_override_device_context():
    old = ['ntp server vrf MGMT 192.0.2.9 source Loopback0']
    commands, _, _, _ = plan('cisco_ios', old, '--source', 'Loopback1', data={
        'source_interface': {}, 'ntp_vrf': 'default'})
    assert commands[:2] == ['ntp server 192.0.2.10 source Loopback1', 'ntp server 192.0.2.11 source Loopback1']
    assert commands[-1] == 'no ' + old[0]


def test_existing_key_binding_is_reused_without_requesting_key_material():
    old = ['ntp authentication-key 7 md5 encrypted 7', 'ntp trusted-key 7', 'ntp authenticate',
           'ntp server vrf MGMT 192.0.2.9 key 7 source Lo0']
    commands, _, _, _ = plan('cisco_ios', old)
    assert commands == ['ntp server vrf MGMT 192.0.2.10 key 7 source Loopback0',
                        'ntp server vrf MGMT 192.0.2.11 key 7 source Loopback0', 'no ' + old[-1]]


def test_different_sources_in_one_vrf_require_selection_for_new_addresses():
    old = ['ntp server vrf MGMT 192.0.2.1 source Lo0', 'ntp server vrf MGMT 192.0.2.2 source Lo1']
    with pytest.raises(ValueError, match='sources differ in VRF MGMT'):
        plan('cisco_ios', old)
    assert 'source Loopback2' in plan('cisco_ios', old, '--source', 'Loopback2')[0][0]


def test_verification_uses_original_context_even_if_device_loses_it():
    commands, _, keys, context = plan('cisco_ios', ['ntp server vrf MGMT 192.0.2.9 source Lo0'])
    after = FEATURE.platforms['cisco_ios'].parse('ntp server 192.0.2.10\nntp server 192.0.2.11')
    add, remove = plan_ntp(after, keys, MODE_REPLACE, {**context, 'verification': True})
    assert len(add) == 2 and len(remove) == 2
    corrections = render('ntp', 'cisco_ios', add, remove, context['variables'])
    assert corrections[:2] == commands[:2]


@pytest.mark.parametrize('mode', [MODE_ADD, MODE_REPLACE])
@pytest.mark.parametrize('global_lines,server,new', [
    (['ntp local-interface vrf MGMT Management1'], 'ntp server vrf MGMT 192.0.2.9 iburst',
     'ntp server vrf MGMT {ip} iburst'),
    ([], 'ntp server vrf MGMT 10.10.10.10 source Management1 key 1',
     'ntp server vrf MGMT {ip} key 1 iburst source Management1'),
])
def test_netbox_with_no_source_tags_or_vrf_can_apply_verify_and_repeat(
        device, login, netbox, tmp_path, mode, global_lines, server, new):
    (tmp_path / 'standards.yaml').write_text('ntp:\n  servers: [192.0.2.10, 192.0.2.11]\n')
    original = [*global_lines, server]
    box = device['devices']['leaf1'] = EosDevice(original)
    report = tmp_path / 'report.json'
    args = ('--limit', 'leaf1', '--' + mode, '--apply', '-y', '--report', str(report))
    assert run_netbox('ntp', *args) == cli.EXIT_OK
    row = json.loads(report.read_text())['devices']['leaf1']
    assert row['verified'] is True and row['saved'] is True
    additions = [new.format(ip=ip) for ip in ('192.0.2.10', '192.0.2.11')]
    assert device['config']['leaf1'] == additions + (['no ' + server] if mode == MODE_REPLACE else [])
    assert box.lines == global_lines + ([server] if mode == MODE_ADD else []) + additions
    sent = list(device['config']['leaf1'])
    assert run_netbox('ntp', *args) == cli.EXIT_OK
    assert device['config']['leaf1'] == sent
    box.apply(row['rollback'])
    assert set(box.lines) == set(original)


def test_junos_reuses_existing_instance_and_source_without_interface_lookup(setup):
    setup.box.ntp = ET.fromstring('<ntp><server><name>192.0.2.9</name><routing-instance>mgmt_junos</routing-instance>'
                                 '</server><source-address><name>198.51.100.55</name>'
                                 '<routing-instance>mgmt_junos</routing-instance></source-address></ntp>')
    code, report = setup.run('--replace', '--apply')
    assert code == cli.EXIT_OK
    row = report['devices']['srx']
    assert row['saved'] and row['verified']
    assert row['config_after']['servers']['192.0.2.10']['vrf'] == 'mgmt_junos'
    assert row['config_after']['sources'] == {'mgmt_junos': '198.51.100.55'}
    assert not any(junos_ntp.INTERFACES in call for call in setup.box.calls)
    setup.box.calls.clear()
    assert setup.run('--replace', '--apply')[0] == cli.EXIT_OK
    assert setup.box.calls == [junos_ntp.SHOW]
