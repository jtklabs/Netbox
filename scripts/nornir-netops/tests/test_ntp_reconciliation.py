"""NTP source overrides, EOS enablement and VRF migration regressions."""
import copy
import json

import pytest

from netops import cli, ntp_nxos
from netops.core import MODE_ADD, MODE_REPLACE, render
from netops.features.ntp import FEATURE, plan_ntp
from netops.ntp_discovery import observe, source_config
from netops.rollback import default_reversal
from test_ntp import parse_args
from test_run import FakeDevice, csv_file, device, login, run


def planned(platform, before, *flags, mode=MODE_REPLACE):
    wanted = FEATURE.build_desired(parse_args(['-s', '192.0.2.1', *flags]))
    current = FEATURE.platforms[platform].parse(before)
    variables = copy.deepcopy(wanted.variables)
    add, remove = plan_ntp(current, wanted.keys, mode, {'platform': platform, 'variables': variables})
    commands = render('ntp', platform, add, remove, variables)
    return commands, default_reversal(commands, current, remove)


class EosDevice(FakeDevice):
    """Model service enablement and EOS's single-VRF constraint independently."""
    def __init__(self, lines, *, ignore_enable=False):
        super().__init__(lines)
        self.ignore_enable = ignore_enable

    def apply(self, commands):
        for command in commands:
            if command == 'ntp':
                if not self.ignore_enable:
                    self.lines = [line for line in self.lines if line != 'no ntp']
            elif command == 'no ntp':
                if command not in self.lines:
                    self.lines.append(command)
            else:
                if command.startswith('ntp server '):
                    def vrf(line):
                        tokens = line.split()
                        return tokens[3] if tokens[2] == 'vrf' else 'default'
                    assert all(vrf(line) == vrf(command) for line in self.lines
                               if line.startswith('ntp server ')), 'EOS cannot mix NTP VRFs'
                super().apply([command])


@pytest.mark.parametrize('address', ['198.51.100.55', '2001:db8::55'])
def test_eos_source_override_replaced_verified_and_restorable(address):
    original = f'ntp server 192.0.2.1 source-address {address} iburst version 3 minpoll 6'
    box = EosDevice(['ntp local-interface Loopback0', original])
    commands, reversal = planned('arista_eos', '\n'.join(box.lines), '--source', 'Loopback0')
    assert commands == ['no ' + original, 'ntp server 192.0.2.1 iburst source Loopback0 version 3 minpoll 6']
    box.apply(commands)
    assert planned('arista_eos', '\n'.join(box.lines), '--source', 'Loopback0')[0] == []
    box.apply(reversal.commands)
    assert set(box.lines) == {'ntp local-interface Loopback0', original}


def test_basic_eos_update_preserves_existing_source_address():
    before = 'ntp server 192.0.2.1 source-address 198.51.100.55 version 3'
    commands, _ = planned('arista_eos', before)
    assert commands == ['no ' + before, 'ntp server 192.0.2.1 iburst version 3 source-address 198.51.100.55']
    box = EosDevice([before])
    box.apply(commands)
    assert planned('arista_eos', '\n'.join(box.lines))[0] == []


@pytest.mark.parametrize('platform,config', [
    ('arista_eos', 'ntp local-interface Loopback0\nntp server 192.0.2.1 source-address 198.51.100.55'),
    ('cisco_nxos', 'ntp source 198.51.100.55\nntp source-interface mgmt0\nntp server 192.0.2.1'),
])
def test_source_ip_discovery_never_infers_an_interface(platform, config):
    commands = []
    def read(command):
        commands.append(command)
        return FakeDevice(config.splitlines()).show(command)
    result = observe(platform, read)
    assert result['status'] == 'ambiguous'
    assert result['servers'][0]['source_address'] == '198.51.100.55'
    assert 'source' not in result
    assert not any('show running-config interface' in command for command in commands)


@pytest.mark.parametrize('address', ['192.0.2.254', '2001:db8::254'])
@pytest.mark.parametrize('mode', [MODE_ADD, MODE_REPLACE])
def test_nxos_basic_update_preserves_global_source_ip(address, mode):
    before = f'ntp source {address}\nntp server 192.0.2.1 use-vrf management'
    assert planned('cisco_nxos', before, '--vrf', 'management', mode=mode)[0] == []
    commands, _ = planned('cisco_nxos', before, '--vrf', 'management', '--prefer', '192.0.2.1', mode=mode)
    assert commands == ['no ntp server 192.0.2.1 use-vrf management',
                        'ntp server 192.0.2.1 prefer use-vrf management']
    assert source_config(before, 'cisco_nxos')['status'] == 'ambiguous'


@pytest.mark.parametrize('existing_interface', ['', 'ntp source-interface mgmt0\n'])
def test_nxos_selected_interface_replaces_global_source_ip_and_can_roll_back(existing_interface):
    before = existing_interface + 'ntp source 192.0.2.254\nntp server 192.0.2.1 use-vrf management'
    commands, reversal = planned('cisco_nxos', before, '--source', 'mgmt0', '--vrf', 'management')
    assert commands == ['no ntp source 192.0.2.254'] + ([] if existing_interface else ['ntp source-interface mgmt0'])
    box = FakeDevice(before.splitlines())
    box.apply(commands)
    assert planned('cisco_nxos', '\n'.join(box.lines), '--source', 'mgmt0', '--vrf', 'management')[0] == []
    box.apply(reversal.commands)
    assert set(box.lines) == set(before.splitlines())


@pytest.mark.parametrize('config', ['ntp source', 'ntp source not-an-ip',
                                  'ntp source 192.0.2.1\nntp source 192.0.2.2',
                                  'ntp server 192.0.2.1 source-address',
                                  'ntp server 192.0.2.1 source-address not-an-ip'])
def test_invalid_address_sources_still_fail_before_planning(config):
    with pytest.raises(ValueError):
        ntp_nxos.parse(config)


@pytest.mark.parametrize('old_vrf,new_vrf', [(None, 'MGMT'), ('MGMT', None), ('OLD', 'NEW')])
def test_eos_vrf_migration_requires_replace_and_round_trips(old_vrf, new_vrf):
    original = 'ntp server ' + (f'vrf {old_vrf} ' if old_vrf else '') + '192.0.2.9 iburst'
    flags = ['--vrf', new_vrf] if new_vrf else []
    with pytest.raises(ValueError, match='one VRF'):
        planned('arista_eos', original, *flags, mode=MODE_ADD)
    commands, reversal = planned('arista_eos', original, *flags)
    box = EosDevice([original])
    box.apply(commands)
    assert planned('arista_eos', '\n'.join(box.lines), *flags)[0] == []
    box.apply(reversal.commands)
    assert box.lines == [original]


@pytest.mark.parametrize('ignore_enable', [False, True])
def test_eos_cli_enables_reads_back_and_only_saves_if_enabled(device, csv_file, login, tmp_path, ignore_enable):
    original = ['no ntp', 'ntp server 192.0.2.1 iburst']
    box = device['devices']['leaf1'] = EosDevice(original, ignore_enable=ignore_enable)
    report = tmp_path / 'ntp.json'
    args = ['-s', '192.0.2.1', '--limit', 'leaf1', '--report', str(report)]
    assert run(csv_file, *args, '--fail-on-diff') == cli.EXIT_DIFF
    assert device['config'] == {}
    assert box.lines == original
    code = run(csv_file, *args, '--apply', '-y')
    row = json.loads(report.read_text())['devices']['leaf1']
    assert code == (cli.EXIT_FAILED if ignore_enable else cli.EXIT_OK)
    assert row['verified'] is not ignore_enable
    assert row['saved'] is not ignore_enable
    assert device['config']['leaf1'] == ['ntp']
    if ignore_enable:
        assert 'ntp:enabled' in row['missing_after']
        assert 'write memory' not in device['commands']['leaf1']
    else:
        assert run(csv_file, *args, '--apply', '-y') == cli.EXIT_OK
        assert device['config']['leaf1'] == ['ntp']  # second apply is a no-op
        box.apply(row['rollback'])
        assert set(box.lines) == set(original)


def test_eos_cross_vrf_add_fails_before_any_cli_write(device, csv_file, login):
    box = device['devices']['leaf1'] = EosDevice(['ntp server vrf OLD 192.0.2.9 iburst'])
    assert run(csv_file, '-s', '192.0.2.1', '--limit', 'leaf1', '--vrf', 'NEW', '--apply', '-y') == cli.EXIT_FAILED
    assert device['config'] == {}
    assert box.lines == ['ntp server vrf OLD 192.0.2.9 iburst']


@pytest.mark.parametrize('platform,before', [
    ('arista_eos', ['ntp local-interface Loopback0',
                    'ntp server 192.0.2.1 source-address 198.51.100.55 iburst']),
    ('cisco_nxos', ['ntp source 198.51.100.55', 'ntp source-interface Loopback0',
                    'ntp server 192.0.2.1']),
])
def test_ignored_source_correction_fails_verification_without_saving(device, login, tmp_path, platform, before):
    class IgnoringDevice(FakeDevice):
        def apply(self, commands):
            pass  # A device accepted the connection but did not change config.

    device['devices']['switch'] = IgnoringDevice(before)
    inventory = tmp_path / 'hosts.csv'
    inventory.write_text(f'host,name,platform\n192.0.2.10,switch,{platform}\n')
    report = tmp_path / 'report.json'
    assert run(str(inventory), '-s', '192.0.2.1', '--source', 'Loopback0',
               '--apply', '-y', '--report', str(report)) == cli.EXIT_FAILED
    row = json.loads(report.read_text())['devices']['switch']
    assert row['verified'] is False and row['saved'] is False
    assert row['missing_after']
    assert all(command.startswith('show ') for command in device['commands']['switch'])
