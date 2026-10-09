"""One shared interface selection, with fail-closed legacy compatibility."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nornir.core.task import MultiResult, Result

from netops import cli, collect, ntp_discovery, syslog_discovery
from netops.netbox import Client as NetBoxClient, DEFAULT_SOURCE_TAGS, NetBoxError, NetBoxInventory, source_interfaces, source_tags
from test_netbox import FakeClient, device, interface
from test_ntp_discovery import Client


@pytest.mark.parametrize('existing', [[], ['service-source'], ['ntp-source'], ['syslog-source']])
def test_missing_source_tags_never_reach_validated_interface_filter(existing):
    calls = []

    def get(url, params, **kwargs):
        calls.append((url, params))
        if url.endswith('/extras/tags/'):
            rows = [{'slug': params['slug']}] if params['slug'] in existing else []
        else:
            assert params['tag'] in existing, 'NetBox rejects nonexistent interface tag filters'
            rows = [interface('Loopback0')]
        return SimpleNamespace(status_code=200, json=lambda: {'results': rows, 'next': None})

    client = NetBoxClient('https://nb', 'test-token')
    client._session = SimpleNamespace(get=get)
    result = source_interfaces(client, DEFAULT_SOURCE_TAGS)
    assert result == ({1: {'source_interface': {'ntp': 'Loopback0', 'syslog': 'Loopback0'}}} if existing else {})
    assert len([url for url, _ in calls if url.endswith('/dcim/interfaces/')]) == len(existing)


@pytest.mark.parametrize('status', [403, 500])
@pytest.mark.parametrize('endpoint', ['extras/tags/', 'dcim/interfaces/'])
def test_source_lookup_errors_are_not_treated_as_missing_tags(status, endpoint):
    def get(url, **kwargs):
        if url.endswith('/' + endpoint):
            return SimpleNamespace(status_code=status, text='lookup failed')
        return SimpleNamespace(status_code=200, json=lambda: {
            'results': [{'slug': 'service-source'}], 'next': None})

    client = NetBoxClient('https://nb', 'test-token')
    client._session = SimpleNamespace(get=get)
    with pytest.raises(NetBoxError, match=f'failed \\({status}\\)'):
        source_interfaces(client, DEFAULT_SOURCE_TAGS)


def test_shared_tag_drives_both_features_and_is_queried_only_once():
    client = FakeClient([device()], {'service-source': [interface('Loopback0')]})
    host = NetBoxInventory(client=client).load().hosts['sw1']
    assert host.data['source_interface'] == {'ntp': 'Loopback0', 'syslog': 'Loopback0'}
    assert len([call for call in client.calls if call == ('dcim/interfaces/', {'tag': 'service-source'})]) == 1
    assert source_tags(['service-source']) == DEFAULT_SOURCE_TAGS


def test_missing_custom_source_tag_fails_instead_of_ignoring_override():
    with pytest.raises(NetBoxError, match='does not exist'):
        source_interfaces(FakeClient(), {'ntp': 'clock-source'})


def test_legacy_same_interface_is_deduplicated_for_both_services():
    client = FakeClient([device()], {'ntp-source': [interface('Loopback0')],
                                      'syslog-source': [interface('Loopback0')]})
    host = NetBoxInventory(client=client).load().hosts['sw1']
    assert host.data['source_interface'] == {'ntp': 'Loopback0', 'syslog': 'Loopback0'}
    assert host.data['source_interface_error'] == {}


def test_conflicting_legacy_tags_block_both_unless_shared_selection_is_explicit():
    client = FakeClient([device()], {'ntp-source': [interface('Loopback0')],
                                      'syslog-source': [interface('Loopback1')]})
    host = NetBoxInventory(client=client).load().hosts['sw1']
    assert host.data['source_interface'] == {}
    assert set(host.data['source_interface_error']) == {'ntp', 'syslog'}
    client.interfaces['service-source'] = [interface('Loopback2')]
    host = NetBoxInventory(client=client).load().hosts['sw1']
    assert host.data['source_interface'] == {'ntp': 'Loopback2', 'syslog': 'Loopback2'}


def test_multiple_shared_tags_do_not_fall_back_to_legacy():
    client = FakeClient([device()], {'service-source': [interface('Loopback0'), interface('Loopback1')],
                                      'ntp-source': [interface('Loopback2')]})
    host = NetBoxInventory(client=client).load().hosts['sw1']
    assert host.data['source_interface'] == {}
    assert all('2 interfaces are tagged service-source' in problem
               for problem in host.data['source_interface_error'].values())


@pytest.mark.parametrize('module', [ntp_discovery, syslog_discovery])
def test_discovery_does_not_override_other_services_legacy_tag(module):
    client = Client()
    client.interfaces.append({'id': 5, 'device': {'id': 1}, 'name': 'Loopback1',
                              'tags': [{'id': 9, 'slug': 'syslog-source'}]})
    observation = {'status': 'resolved', 'source': 'Loopback0', 'vrf': 'default'}
    assert module.sync_device(client, SimpleNamespace(data={'netbox_id': 1}), observation)['status'] == 'attention'
    assert len(client.writes) == 1  # observation only


def test_collector_conflicting_observations_never_choose_first_source(monkeypatch, tmp_path):
    client = Client()
    host = SimpleNamespace(name='sw1', data={'netbox_id': 1})
    data = {'platform': 'cisco_ios', 'outputs': [],
            'ntp_discovery': {'status': 'resolved', 'source': 'Loopback0', 'vrf': 'default'},
            'syslog_discovery': {'status': 'resolved', 'source': 'Loopback1', 'vrf': 'default'}}
    result = MultiResult('collect')
    result.append(Result(host=host, result=data))
    targets = SimpleNamespace(inventory=SimpleNamespace(hosts={'sw1': host}),
                              run=Mock(return_value={'sw1': result}))
    monkeypatch.setattr(cli, '_connect', lambda args, style: (targets, SimpleNamespace(describe=lambda: 'test'), 0))
    monkeypatch.setattr(collect, 'netbox_client', lambda args: client)
    monkeypatch.setattr(collect, 'upload', Mock(return_value=(0, 0)))
    monkeypatch.setattr(collect.archive, 'capture', Mock())
    args = cli.build_parser().parse_args(['collect', '--netbox', '--no-standards', '--output-dir', str(tmp_path)])
    assert collect.run(args, cli.Style(False), Mock()) == cli.EXIT_DIFF
    assert not any(tag['slug'] == 'service-source' for tag in client.interfaces[0]['tags'])
    assert 'ntp_vrf' not in client.device['custom_fields']
    assert 'syslog_vrf' not in client.device['custom_fields']
    assert client.device['custom_fields']['ntp_discovery']['status'] == 'ambiguous'
    assert client.device['custom_fields']['syslog_discovery']['status'] == 'ambiguous'
