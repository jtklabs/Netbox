from copy import deepcopy

import pytest

from netops import collect, syslog_discovery as discovery
from netops.cli import build_parser
from test_collect import make_task
from test_ntp_discovery import Client


@pytest.mark.parametrize('platform,config,source,vrf', [
    ('cisco_ios', 'logging host 192.0.2.50\nlogging source-interface Lo0', 'Loopback0', 'default'),
    ('cisco_xe', 'logging host 192.0.2.50 vrf MGMT\nlogging source-interface Gi0/0 vrf MGMT',
     'GigabitEthernet0/0', 'MGMT'),
    ('arista_eos', 'logging vrf MGMT host 192.0.2.50\nlogging vrf MGMT local-interface Management1',
     'Management1', 'MGMT'),
    ('arista_eos', 'logging host 192.0.2.50\nlogging source-interface vlan 25', 'Vlan25', 'default'),
    ('cisco_nxos', 'logging server 192.0.2.50 6 use-vrf management\nlogging source-interface mgmt0',
     'mgmt0', 'management'),
])
def test_platform_sources(platform, config, source, vrf):
    found = discovery.source_config(config, platform)
    assert found['status'] == 'resolved'
    assert (found['source'], found['vrf']) == (source, vrf)


@pytest.mark.parametrize('config', [
    'logging host 192.0.2.50',
    'logging host 192.0.2.50\nlogging host 192.0.2.51 vrf MGMT\nlogging source-interface Lo0',
    'logging host 192.0.2.50 vrf MGMT\nlogging source-interface Lo0',
])
def test_no_guessing_sources_or_vrfs(config):
    assert discovery.source_config(config, 'cisco_ios')['status'] == 'ambiguous'


@pytest.mark.parametrize('config', [
    'logging source-interface', 'logging source-interface Lo0;reload',
    'logging host 192.0.2.50 vrf', 'logging host',
    'logging source-interface Lo0\nlogging source-interface Lo1',
])
def test_malformed_sources_fail_closed(config):
    with pytest.raises(ValueError):
        discovery.source_config(config, 'cisco_ios')


def test_nxos_missing_vrf_uses_operational_confirmation():
    config = 'logging server 192.0.2.50\nlogging source-interface Lo0'
    assert discovery.source_config(config, 'cisco_nxos')['status'] == 'ambiguous'
    found = discovery.source_config(config, 'cisco_nxos', {'192.0.2.50': {'vrf': 'default'}})
    assert found['status'] == 'resolved' and found['vrf'] == 'default'
    with pytest.raises(ValueError, match='disagree'):
        discovery.source_config(config.replace('192.0.2.50', '192.0.2.50 use-vrf management'),
                                'cisco_nxos', {'192.0.2.50': {'vrf': 'default'}})


def test_collector_runs_read_only_and_records_syslog_independently(tmp_path):
    task, connection = make_task(replies={
        discovery.SHOW_COMMAND: 'logging host 192.0.2.50\nlogging source-interface Lo0',
        'show running-config interface Loopback0': 'interface Loopback0\n!',
    })
    result = collect.collect_commands(task, collect.load_catalog(), tmp_path)
    assert result.result['syslog_discovery']['status'] == 'resolved'
    assert result.result['ntp_discovery']['status'] == 'unconfigured'
    assert result.result['outputs']
    assert all(command.startswith('show ') for command, _ in connection.sent)
    assert not result.changed


def test_collector_disable_flag_and_interface_vrf_mismatch(tmp_path):
    task, connection = make_task(replies={
        discovery.SHOW_COMMAND: 'logging host 192.0.2.50\nlogging source-interface Lo0',
        'show running-config interface Loopback0': 'interface Loopback0\n vrf forwarding MGMT',
    })
    assert collect.collect_commands(task, collect.load_catalog(), tmp_path).result['syslog_discovery']['status'] == 'ambiguous'
    connection.sent.clear()
    result = collect.collect_commands(task, collect.load_catalog(), tmp_path, discover_syslog=False)
    assert 'syslog_discovery' not in result.result
    assert discovery.SHOW_COMMAND not in [command for command, _ in connection.sent]
    assert build_parser().parse_args(['collect', '--no-syslog-discovery']).no_syslog_discovery


def test_syslog_bootstrap_keeps_ntp_selection_and_is_idempotent():
    client = Client()
    client.device['custom_fields']['ntp_vrf'] = 'NTP-VRF'
    client.interfaces[0]['tags'].append({'id': 3, 'slug': 'ntp-source'})
    task, _ = make_task()
    task.host.data['netbox_id'] = 1
    observation = discovery.source_config('logging host 192.0.2.50\nlogging source-interface Lo0', 'cisco_ios')
    discovery.ensure_fields(client)
    assert {field['name'] for field in client.fields} == {'syslog_discovery', 'syslog_vrf'}
    assert discovery.sync_device(client, task.host, observation)['status'] == 'written'
    assert {tag['slug'] for tag in client.interfaces[0]['tags']} == {'unrelated', 'ntp-source', 'service-source'}
    assert client.device['custom_fields']['syslog_vrf'] == 'default'
    assert client.device['custom_fields']['ntp_vrf'] == 'NTP-VRF'
    client.writes.clear()
    assert discovery.sync_device(client, task.host, observation)['status'] == 'unchanged'
    assert len(client.writes) == 1


@pytest.mark.parametrize('conflict', ['source', 'vrf', 'multiple', 'missing', 'error'])
def test_syslog_conflicts_never_move_or_clear_tags(conflict):
    client = Client()
    tag = {'id': 8, 'slug': 'syslog-source'}
    observation = discovery.source_config('logging host 192.0.2.50\nlogging source-interface Lo0', 'cisco_ios')
    if conflict == 'source':
        client.interfaces.append({'id': 5, 'device': {'id': 1}, 'name': 'Loopback1', 'tags': [tag]})
    elif conflict == 'vrf':
        client.device['custom_fields']['syslog_vrf'] = 'MANUAL'
    elif conflict == 'multiple':
        client.interfaces.append({**client.interfaces[0], 'id': 5, 'name': 'Lo0'})
    elif conflict == 'missing':
        client.interfaces = []
    else:
        observation = {'status': 'error', 'reason': 'SSH timeout'}
    before = deepcopy(client.interfaces)
    task, _ = make_task()
    task.host.data['netbox_id'] = 1
    assert discovery.sync_device(client, task.host, observation)['status'] == 'attention'
    assert client.interfaces == before
    assert len(client.writes) == 1
    assert set(client.writes[0][2]['custom_fields']) == {'syslog_discovery'}


@pytest.mark.parametrize('preview', [False, True])
def test_collector_syslog_writeback_survives_ntp_failure(monkeypatch, tmp_path, preview):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from nornir.core.task import MultiResult, Result
    from netops import cli
    client = Client()
    host = SimpleNamespace(name='sw1', data={'netbox_id': 1})
    data = {'platform': 'cisco_ios', 'outputs': [],
            'ntp_discovery': {'status': 'error', 'reason': 'NTP read failed'},
            'syslog_discovery': discovery.source_config(
                'logging host 192.0.2.50\nlogging source-interface Lo0', 'cisco_ios')}
    result = MultiResult('collect')
    result.append(Result(host=host, result=data))
    targets = SimpleNamespace(inventory=SimpleNamespace(hosts={'sw1': host}),
                              run=Mock(return_value={'sw1': result}))
    monkeypatch.setattr(cli, '_connect', lambda args, style: (targets, SimpleNamespace(describe=lambda: 'test'), 0))
    monkeypatch.setattr(collect, 'netbox_client', lambda args: client)
    monkeypatch.setattr(collect, 'upload', Mock(return_value=(0, 0)))
    monkeypatch.setattr(collect.archive, 'capture', Mock())
    args = build_parser().parse_args(['collect', '--netbox', '--no-standards', '--output-dir', str(tmp_path),
                                     '--syslog-source-tag', 'logging-source'] + (['--no-upload'] if preview else []))
    assert collect.run(args, cli.Style(False), Mock()) == cli.EXIT_FAILED
    if preview:
        assert client.writes == []
    else:
        assert any(tag['slug'] == 'logging-source' for tag in client.interfaces[0]['tags'])
        assert client.device['custom_fields']['syslog_vrf'] == 'default'
        assert 'ntp_vrf' not in client.device['custom_fields']
