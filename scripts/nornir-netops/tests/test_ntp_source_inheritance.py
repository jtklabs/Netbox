"""Reuse defined NTP sources without requiring inventory selections."""
import json

import pytest

from netops import cli
from netops.core import MODE_ADD, MODE_REPLACE
from test_ntp_inheritance import plan
from test_ntp_reconciliation import EosDevice
from test_run import FakeDevice, device, login, netbox, run_netbox


@pytest.mark.parametrize('platform,source,new', [
    ('cisco_ios', 'source Lo0', 'ntp server vrf MGMT {ip} source Loopback0'),
    ('arista_eos', 'source Management1', 'ntp server vrf MGMT {ip} iburst source Management1'),
    ('arista_eos', 'source-address 198.51.100.55',
     'ntp server vrf MGMT {ip} iburst source-address 198.51.100.55'),
])
@pytest.mark.parametrize('mode', [MODE_ADD, MODE_REPLACE])
@pytest.mark.parametrize('reverse', [False, True])
def test_new_servers_reuse_defined_source_when_other_servers_have_none(platform, source, new, mode, reverse):
    original = ['ntp server vrf MGMT 192.0.2.8', 'ntp server vrf MGMT 192.0.2.9 ' + source]
    if reverse:
        original.reverse()
    commands, reversal, _, _ = plan(platform, original, mode=mode)
    additions = [new.format(ip=ip) for ip in ('192.0.2.10', '192.0.2.11')]
    assert commands == additions + (['no ' + line for line in original] if mode == MODE_REPLACE else [])
    box = EosDevice(original) if platform == 'arista_eos' else FakeDevice(original)
    box.apply(commands)
    assert set(box.lines) == set(additions + (original if mode == MODE_ADD else []))
    assert plan(platform, box.lines, mode=mode)[0] == []
    box.apply(reversal.commands)
    assert set(box.lines) == set(original)


def test_existing_unsourced_endpoint_keeps_its_settings_while_new_endpoint_inherits():
    original = ['ntp server vrf MGMT 192.0.2.10', 'ntp server vrf MGMT 192.0.2.9 source Lo0']
    commands, _, _, _ = plan('cisco_ios', original)
    assert commands == ['ntp server vrf MGMT 192.0.2.11 source Loopback0', 'no ' + original[1]]


def test_no_defined_source_does_not_invent_one():
    commands, _, _, _ = plan('arista_eos', ['ntp server vrf MGMT 192.0.2.9'])
    assert commands[:2] == ['ntp server vrf MGMT 192.0.2.10 iburst',
                            'ntp server vrf MGMT 192.0.2.11 iburst']


def test_source_is_not_borrowed_from_another_vrf():
    original = ['ntp server vrf MGMT 192.0.2.8', 'ntp server vrf OTHER 192.0.2.9 source Lo0']
    commands, _, _, _ = plan('cisco_ios', original)
    assert commands[:4] == [
        'ntp server vrf MGMT 192.0.2.10', 'ntp server vrf OTHER 192.0.2.10 source Loopback0',
        'ntp server vrf MGMT 192.0.2.11', 'ntp server vrf OTHER 192.0.2.11 source Loopback0',
    ]


@pytest.mark.parametrize('platform,sources', [
    ('cisco_ios', ['source Lo0', 'source Lo1']),
    ('arista_eos', ['source Management1', 'source Management2']),
    ('arista_eos', ['source-address 198.51.100.55', 'source-address 198.51.100.56']),
])
def test_multiple_defined_sources_remain_ambiguous_even_with_unsourced_server(platform, sources):
    original = ['ntp server vrf MGMT 192.0.2.8'] + [
        f'ntp server vrf MGMT 192.0.2.{number} {source}' for number, source in enumerate(sources, 1)]
    with pytest.raises(ValueError, match='sources differ in VRF MGMT'):
        plan(platform, original)


@pytest.mark.parametrize('mode', [MODE_ADD, MODE_REPLACE])
def test_netbox_job_applies_and_verifies_mixed_sources_without_tags(device, login, netbox, tmp_path, mode):
    (tmp_path / 'standards.yaml').write_text('ntp:\n  servers: [192.0.2.10, 192.0.2.11]\n')
    original = ['ntp server vrf MGMT 192.0.2.8',
                'ntp server vrf MGMT 192.0.2.9 source Management1']
    box = device['devices']['leaf1'] = EosDevice(original)
    report = tmp_path / 'report.json'
    args = ('--limit', 'leaf1', '--' + mode, '--apply', '-y', '--report', str(report))
    assert run_netbox('ntp', *args) == cli.EXIT_OK
    row = json.loads(report.read_text())['devices']['leaf1']
    assert row['saved'] and row['verified']
    additions = [f'ntp server vrf MGMT {ip} iburst source Management1'
                 for ip in ('192.0.2.10', '192.0.2.11')]
    assert device['config']['leaf1'] == additions + (['no ' + line for line in original] if mode == MODE_REPLACE else [])
    assert set(box.lines) == set(additions + (original if mode == MODE_ADD else []))
    sent = list(device['config']['leaf1'])
    assert run_netbox('ntp', *args) == cli.EXIT_OK
    assert json.loads(report.read_text())['devices']['leaf1']['compliant']
    assert device['config']['leaf1'] == sent
