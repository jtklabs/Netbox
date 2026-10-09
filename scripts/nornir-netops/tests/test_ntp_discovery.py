from copy import deepcopy
from types import SimpleNamespace

import pytest

from netops.cli import build_parser
from netops.netbox import NetBoxError
from netops.ntp_discovery import discover, ensure_fields, interface_vrf, source_config, sync_device


@pytest.mark.parametrize('platform,config,source,vrf', [
    ('cisco_ios', 'ntp source Loopback0\nntp server 192.0.2.1', 'Loopback0', 'default'),
    ('cisco_ios', 'ntp server vrf MGMT 192.0.2.1 source Gi0/0', 'GigabitEthernet0/0', 'MGMT'),
    ('arista_eos', 'ntp local-interface vrf MGMT Management1\nntp server vrf MGMT 192.0.2.1', 'Management1', 'MGMT'),
    ('arista_eos', 'ntp local-interface vlan 25\nntp server 192.0.2.1', 'Vlan25', 'default'),
    ('arista_eos', 'ntp server 192.0.2.1 source Ethernet1/1', 'Ethernet1/1', 'default'),
    ('cisco_ios', 'ntp source Loopback0\nntp server 192.0.2.1 source Loopback1', 'Loopback1', 'default'),
])
def test_explicit_sources(platform, config, source, vrf):
    found = source_config(config, platform)
    assert found['status'] == 'resolved'
    assert (found['source'], found['vrf']) == (source, vrf)


@pytest.mark.parametrize('config', [
    'ntp server 192.0.2.1',
    'ntp server 192.0.2.1 source Lo0\nntp server 192.0.2.2 source Lo1',
    'ntp server 192.0.2.1 source Lo0\nntp server vrf MGMT 192.0.2.2 source Lo0',
    'ntp server 192.0.2.1 source Lo0\nntp server 192.0.2.2',
])
def test_does_not_guess_an_egress_or_choose_between_sources(config):
    assert source_config(config, 'cisco_ios')['status'] == 'ambiguous'


def test_no_server_and_authentication_secrets_are_not_observations():
    assert source_config('ntp source Lo0', 'cisco_ios')['status'] == 'unconfigured'
    found = source_config('ntp authentication-key 1 md5 secret\nntp server 192.0.2.1 source Lo0', 'cisco_ios')
    assert 'secret' not in str(found)


@pytest.mark.parametrize('config', ['ntp server', 'ntp server vrf MGMT', 'ntp server 192.0.2.1 source',
                                  'ntp source', 'ntp source Lo0\nntp source Lo1',
                                  'ntp server 192.0.2.1 source Lo0;reload'])
def test_malformed_source_fails_closed(config):
    with pytest.raises(ValueError):
        source_config(config, 'cisco_ios')


@pytest.mark.parametrize('line,expected', [('ip vrf forwarding MGMT', 'MGMT'),
                                         ('vrf forwarding MGMT', 'MGMT'), ('vrf MGMT', 'MGMT'), ('', 'default')])
def test_interface_vrf(line, expected):
    assert interface_vrf(f'interface Management1\n {line}\n!', 'Management1') == expected


@pytest.mark.parametrize('config', ['', 'interface Loopback0',
                                  'interface Management1\ninterface Ethernet1',
                                  'interface Management1\nvrf A\nvrf B'])
def test_interface_missing_or_ambiguous_fails_closed(config):
    with pytest.raises(ValueError):
        interface_vrf(config, 'Management1')


class Task:
    def __init__(self, platform='cisco_ios', interface='interface Loopback0\n!'):
        self.host = SimpleNamespace(platform=platform)
        self.commands = []
        self.interface = interface

    def run(self, **kwargs):
        command = kwargs['command_string']
        self.commands.append(command)
        result = (self.interface if command.startswith('show running-config interface') else
                  'ntp server 192.0.2.1' if '^ntp.server' in command else
                  'ntp source Loopback0' if '^ntp.source' in command else '')
        return SimpleNamespace(result=result)


def test_ssh_only_reads_show_commands_and_confirms_vrf():
    task = Task()
    result = discover(task)
    assert not result.changed
    assert result.result['status'] == 'resolved'
    assert all(command.startswith('show ') for command in task.commands)
    assert result.result['checked_at']
    assert discover(Task(interface='interface Loopback0\n vrf forwarding MGMT')).result['status'] == 'ambiguous'


def test_unsupported_platform_never_connects():
    task = Task(platform='paloalto_panos')
    with pytest.raises(ValueError, match='not supported'):
        discover(task)
    assert task.commands == []


class Client:
    def __init__(self):
        self.device = {'id': 1, 'custom_fields': {'unrelated': 'keep'}}
        self.interfaces = [{'id': 2, 'device': {'id': 1}, 'name': 'Loopback0',
                            'tags': [{'id': 4, 'slug': 'unrelated'}]}]
        self.tags = [{'id': 3, 'slug': 'ntp-source'}]
        self.fields = []
        self.writes = []

    def get(self, path, params):
        if path == 'extras/custom-fields/':
            return deepcopy([field for field in self.fields if field['name'] == params['name']])
        if path == 'dcim/interfaces/':
            return deepcopy(self.interfaces)
        if path == 'extras/tags/':
            return deepcopy([tag for tag in self.tags if tag['slug'] == params['slug']])
        raise AssertionError(path)

    def request_object(self, method, path, data=None):
        if method != 'GET':
            self.writes.append((method, path, deepcopy(data)))
        if method == 'POST':
            created = {'id': 10, **data}
            (self.fields if path == 'extras/custom-fields/' else self.tags).append(created)
            return deepcopy(created)
        if path == 'dcim/devices/1/':
            if data:
                self.device['custom_fields'].update(data['custom_fields'])
            return deepcopy(self.device)
        row = next(row for row in self.interfaces if path == f"dcim/interfaces/{row['id']}/")
        if data:
            tags = {tag['id']: tag for tag in row['tags'] + self.tags}
            row['tags'] = [tags[id_] for id_ in data['tags']]
        return deepcopy(row)


def sync(client, config='ntp server 192.0.2.1 source Loopback0', **kwargs):
    return sync_device(client, SimpleNamespace(data={'netbox_id': 1}), source_config(config, 'cisco_ios'), **kwargs)


def test_bootstrap_preserves_unrelated_data_and_is_idempotent():
    client = Client()
    assert sync(client)['status'] == 'written'
    assert client.device['custom_fields']['ntp_vrf'] == 'default'
    assert client.device['custom_fields']['unrelated'] == 'keep'
    assert {tag['slug'] for tag in client.interfaces[0]['tags']} == {'unrelated', 'service-source'}
    client.writes.clear()
    assert sync(client)['status'] == 'unchanged'
    assert len(client.writes) == 1  # refresh observation only
    assert set(client.writes[0][2]['custom_fields']) == {'ntp_discovery'}


@pytest.mark.parametrize('conflict', ['vrf', 'source', 'multiple', 'missing', 'ambiguous', 'unconfigured'])
def test_conflicts_and_unknowns_only_write_observation(conflict):
    client = Client()
    config = 'ntp server 192.0.2.1 source Loopback0'
    if conflict == 'vrf':
        client.device['custom_fields']['ntp_vrf'] = 'MANUAL'
    elif conflict == 'source':
        client.interfaces.append({'id': 5, 'device': {'id': 1}, 'name': 'Loopback1', 'tags': client.tags})
    elif conflict == 'multiple':
        client.interfaces.append({**client.interfaces[0], 'id': 5, 'name': 'Lo0'})
    elif conflict == 'missing':
        client.interfaces = []
    else:
        config = 'ntp server 192.0.2.1' if conflict == 'ambiguous' else ''
    before = deepcopy(client.interfaces)
    assert sync(client, config)['status'] == 'attention'
    assert client.interfaces == before
    assert len(client.writes) == 1
    assert set(client.writes[0][2]['custom_fields']) == {'ntp_discovery'}


def test_custom_source_tag_and_tag_creation():
    client = Client()
    assert sync(client, source_tag='time-source')['status'] == 'written'
    assert any(tag['slug'] == 'time-source' for tag in client.interfaces[0]['tags'])


def test_wrong_device_response_fails_before_tagging():
    client = Client()
    client.interfaces[0]['device']['id'] = 99
    with pytest.raises(NetBoxError, match='another device'):
        sync(client)
    assert len(client.writes) == 1


def test_custom_field_creation_and_definition_validation():
    client = Client()
    ensure_fields(client)
    assert {field['name'] for field in client.fields} == {'ntp_discovery', 'ntp_vrf'}
    assert all(not field['is_cloneable'] for field in client.fields)
    client.writes.clear()
    ensure_fields(client)
    assert client.writes == []
    client.fields[1]['type'] = 'json'
    with pytest.raises(NetBoxError, match='ntp_vrf must be a text'):
        ensure_fields(client)


def test_cli_is_preview_by_default_and_has_no_device_apply_flag():
    parser = build_parser()
    args = parser.parse_args(['discover-ntp', '--netbox'])
    assert not args.sync_netbox
    assert not hasattr(args, 'apply')
    assert parser.parse_args(['discover-ntp', '--netbox', '--sync-netbox']).sync_netbox
    with pytest.raises(SystemExit):
        parser.parse_args(['discover-ntp', '--netbox', '--apply'])


@pytest.mark.parametrize('writeback,failed,expected', [(False, False, 0), (True, False, 0), (True, True, 1)])
def test_command_preview_and_failure_reporting(monkeypatch, writeback, failed, expected):
    from nornir.core.task import MultiResult, Result
    from netops import cli, ntp_discovery
    client = Client()
    host = SimpleNamespace(name='sw1', hostname='192.0.2.10', data={'netbox_id': 1})
    result = MultiResult('discover-ntp')
    observation = source_config('ntp source Lo0\nntp server 192.0.2.1', 'cisco_ios')
    result.append(Result(host=host, result=observation, failed=failed,
                         exception=RuntimeError('SSH unavailable') if failed else None))
    targets = SimpleNamespace(inventory=SimpleNamespace(hosts={'sw1': host}), run=lambda **kwargs: {'sw1': result})
    monkeypatch.setattr(cli, '_connect', lambda args, style: (targets, None, 0))
    records = {}
    monkeypatch.setattr(ntp_discovery.archive, 'capture', lambda data: records.update(data))
    args = build_parser().parse_args(['discover-ntp', '--netbox', '--no-standards'])
    args.sync_netbox = writeback
    args._netbox_client = client
    assert ntp_discovery.run(args, cli.Style(False), None) == expected
    if not writeback:
        assert client.writes == []
    if failed:
        assert records['sw1']['status'] == 'error'
        assert 'ntp_vrf' not in client.device['custom_fields']
