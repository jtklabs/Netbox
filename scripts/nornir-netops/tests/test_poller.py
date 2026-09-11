"""Poller ownership and CLI selection before any device connections."""
import copy
import importlib
import ipaddress
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from netops import cli
from netops.netbox import NetBoxError, NetBoxInventory
from netops.poller import poller_tag, select_devices, settings_from
from test_netbox import device as nb_device
from test_run import device, login, netbox


def tags(*names):
    return [{"slug": 'poller-' + name} for name in names]


def candidate(number, site, owner=None, address=None):
    return nb_device(number, 'sw' + str(number), address or f'192.0.2.{number}/24',
                     site={"id": site}, tags=tags(*owner) if owner else [])


class Topology:
    def __init__(self):
        self.calls = []
        self.regions = [
            {"id": 1, "parent": None, "tags": tags('boston')},
            {"id": 2, "parent": {"id": 1}, "tags": []},
            {"id": 3, "parent": {"id": 2}, "tags": tags('dallas')},
        ]
        self.sites = [
            {"id": 10, "region": {"id": 2}, "tags": []},
            {"id": 11, "region": {"id": 3}, "tags": []},
            {"id": 12, "region": {"id": 3}, "tags": tags('boston')},
            {"id": 13, "region": {"id": 1}, "tags": tags('dallas')},
            {"id": 14, "region": None, "tags": []},
            {"id": 15, "region": None, "tags": tags('dallas', 'boston')},
        ]
        self.prefixes = {
            10: [{"id": 100, "prefix": "198.51.100.0/24", "scope_type": "dcim.location"}],
            12: [{"id": 101, "prefix": "2001:db8::/64"}],
        }
        self.devices = [
            candidate(1, 10),  # region inherited through untagged child
            candidate(2, 11),  # nearest region belongs to Dallas
            candidate(3, 12),  # site overrides region for Boston
            candidate(4, 13),  # site overrides region for Dallas
            candidate(5, 14),  # no owner
            candidate(6, 10, ['dallas']),
            candidate(7, 13, ['boston']),
            candidate(8, 13, ['dallas', 'boston']),
            candidate(9, 15),
            candidate(10, 13, address='198.51.100.10/24'),  # owned-prefix union
            candidate(11, 10, ['dallas'], address='198.51.100.11/24'),
            candidate(12, 14),
        ]
        self.devices[-1].update(primary_ip4=None, primary_ip6={"address": '2001:db8::12/64'},
                                primary_ip={"address": '2001:db8::12/64'})

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        params = params or {}
        if path == 'dcim/regions/': return copy.deepcopy(self.regions)
        if path == 'dcim/sites/': return copy.deepcopy(self.sites)
        if path == 'ipam/prefixes/': return copy.deepcopy(self.prefixes.get(params['site_id'], []))
        if path == 'dcim/devices/':
            rows = self.devices
            if 'site_id' in params: rows = [d for d in rows if d['site']['id'] == params['site_id']]
            if 'tag' in params: rows = [d for d in rows if params['tag'] in [t['slug'] for t in d['tags']]]
            if 'platform' in params: rows = [d for d in rows if d['platform']['slug'] == params['platform']]
            return copy.deepcopy(rows)
        if path == 'dcim/interfaces/': return []
        if path == 'extras/tags/': return tags('boston', 'dallas')
        if path == 'ipam/ip-addresses/':
            network = ipaddress.ip_network(params['parent'])
            return [d.get('primary_ip4') or d['primary_ip6'] for d in self.devices
                    if ipaddress.ip_address((d.get('primary_ip4') or d['primary_ip6'])['address'].split('/')[0]) in network]
        raise AssertionError((path, params))

    def all(self, path, params=None):
        return self.get(path.lstrip('/'), params)

    def tag_exists(self, tag):
        return tag in ('poller-boston', 'poller-dallas')


@pytest.mark.parametrize('name', ['boston', 'Boston', ' poller-boston '])
def test_matches_scanner_ownership_precedence_prefix_union_and_dual_tags(name):
    topology = Topology()
    chosen, reasons = select_devices(topology, topology.devices, name)
    assert [d['id'] for d in chosen] == [1, 3, 7, 8, 9, 10, 12]
    assert reasons[1] == {'source': 'region-tag', 'region_id': 1, 'site_id': 10, 'poller_tag': 'poller-boston'}
    assert reasons[3]['source'] == 'site-tag'
    assert reasons[7]['source'] == 'device-tag'
    assert reasons[10]['prefix_id'] == 100 and reasons[10]['source'] == 'prefix-region-tag'
    assert reasons[12]['prefix'] == '2001:db8::/64'
    assert [p for p, q in topology.calls].count('dcim/regions/') == 1
    assert [p for p, q in topology.calls].count('dcim/sites/') == 1
    assert all('brief' not in q for p, q in topology.calls)
    assert not any(p == 'ipam/ip-addresses/' for p, q in topology.calls)


@pytest.mark.parametrize('family', [4, 6])
def test_selected_management_addresses_match_actual_snmp_inventory(monkeypatch, family):
    scanner = Path(__file__).resolve().parents[2] / 'snmp-inventory'
    if not scanner.is_dir():
        pytest.skip('cross-project parity check requires sibling snmp-inventory source')
    monkeypatch.syspath_prepend(str(scanner))
    selection = importlib.import_module('snmpinv.selection')
    topology = Topology()
    from netops.netbox import _address
    # The scanner sorts one address family at a time; mixed-family support is
    # exercised directly above without inheriting its sorting limitation.
    topology.devices = [d for d in topology.devices if ipaddress.ip_address(_address(d)).version == family]
    actual = {t.address for t in selection.select_targets(topology, 'boston')}
    chosen, _ = select_devices(topology, topology.devices, 'boston')
    from netops.netbox import _address
    assert {_address(d) for d in chosen} == actual


@pytest.mark.parametrize('name', ['', None, 'poller-', 'two names', 'boston*'])
def test_missing_or_invalid_identity_fails_closed(name):
    with pytest.raises(NetBoxError, match='requires --poller'):
        poller_tag(name)


@pytest.mark.parametrize('mutation', ['region-cycle', 'missing-region', 'brief-device', 'brief-site', 'bad-prefix', 'request-failed'])
def test_unreadable_ownership_fails_closed(mutation):
    topology = Topology()
    if mutation == 'region-cycle':
        topology.regions[0].update(parent={'id': 2}, tags=[])
    elif mutation == 'missing-region': topology.regions.pop(0)
    elif mutation == 'brief-device': topology.devices[0].pop('tags')
    elif mutation == 'brief-site': topology.sites[0].pop('region')
    elif mutation == 'bad-prefix': topology.prefixes[10][0]['prefix'] = 'garbage'
    else:
        def failed(*args): raise NetBoxError('NetBox GET forbidden')
        topology.get = failed
    with pytest.raises(NetBoxError):
        select_devices(topology, topology.devices, 'boston')


def test_unowned_and_other_family_prefixes_do_not_select_anything():
    topology = Topology()
    chosen, _ = select_devices(topology, topology.devices, 'unknown')
    assert chosen == []
    assert not any(p == 'ipam/prefixes/' for p, q in topology.calls)


def test_autofilter_off_keeps_inventory_and_performs_no_ownership_queries():
    topology = Topology()
    inventory = NetBoxInventory(client=topology, source_tags={}).load()
    assert len(inventory.hosts) == len(topology.devices)
    assert topology.calls == [('dcim/devices/', {'status': 'active', 'has_primary_ip': 'true'})]


def test_existing_api_filters_intersect_ownership_before_source_interfaces():
    topology = Topology()
    topology.devices[0]['platform']['slug'] = 'f5-tmos'
    inventory = NetBoxInventory(client=topology, autofilter=True, poller='boston',
                               filters={'platform': 'f5-tmos'}).load()
    assert list(inventory.hosts) == ['sw1']
    assert inventory.hosts['sw1'].data['poller_selection']['source'] == 'region-tag'
    assert not any(path == 'dcim/interfaces/' for path, params in topology.calls)


def test_no_matches_stops_before_interface_load():
    topology = Topology()
    with pytest.raises(NetBoxError, match='no devices belong to poller-unknown'):
        NetBoxInventory(client=topology, autofilter=True, poller='unknown').load()
    assert not any(path == 'dcim/interfaces/' for path, params in topology.calls)


@pytest.mark.parametrize('enabled,environment,expected', [(None, None, False), (None, 'true', True), (False, 'true', False), (True, 'false', True)])
def test_settings_flag_and_environment_precedence(monkeypatch, enabled, environment, expected):
    monkeypatch.delenv('NETBOX_AUTOFILTER', raising=False)
    if environment is not None: monkeypatch.setenv('NETBOX_AUTOFILTER', environment)
    monkeypatch.setenv('NETOPS_POLLER', 'dallas')
    result = settings_from(SimpleNamespace(netbox_autofilter=enabled, poller='boston'))
    assert result == {'autofilter': expected, 'poller': 'poller-boston' if expected else None}
    if expected:
        assert settings_from(SimpleNamespace(netbox_autofilter=enabled))['poller'] == 'poller-dallas'


@pytest.mark.parametrize('command,flags,expected', [('ntp', [], cli.EXIT_OK), ('ntp', ['--apply', '--yes'], cli.EXIT_OK), ('discover', [], cli.EXIT_OK), ('check-ntp', [], cli.EXIT_DIFF)])
def test_cli_uses_poller_before_device_connections_and_records_selection(device, login, netbox, monkeypatch, tmp_path, capsys, command, flags, expected):
    topology = Topology()
    topology.devices = netbox['devices']
    topology.devices[0].update(site={'id': 10}, tags=[])
    topology.devices[1].update(site={'id': 13}, tags=[])
    topology.devices[2].update(site={'id': 13}, tags=tags('dallas'))
    monkeypatch.setattr('netops.netbox.Client', lambda *a, **kw: topology)
    monkeypatch.setenv('NETOPS_POLLER', 'boston')
    monkeypatch.setenv('NETBOX_AUTOFILTER', 'true')
    report = tmp_path / 'autofilter.json'
    code = cli.main([command, '--no-env-file', '--netbox', '--report', str(report), *flags])
    assert code == expected
    doc = json.loads(report.read_text())
    assert set(doc['devices']) == {'sw1'}
    assert doc['inventory_selection']['selected'] == 1 and doc['inventory_selection']['excluded'] == 2
    assert doc['devices']['sw1']['poller_selection']['source'] == 'region-tag'
    assert 'NetBox autofilter: poller-boston, 1 device(s) selected' in capsys.readouterr().out
    assert set(device['commands']) | set(device['config']) <= {'sw1'}


@pytest.mark.parametrize('flags', [
    ['--netbox', '--netbox-autofilter'],
    ['--ip', '192.0.2.1', '--netbox-autofilter', '--poller', 'boston'],
    ['--csv', 'hosts.csv', '--netbox-autofilter', '--poller', 'boston'],
])
def test_cli_invalid_autofilter_stops_before_authentication(monkeypatch, flags):
    monkeypatch.delenv('NETOPS_POLLER', raising=False)
    def unexpected(*args): raise AssertionError('login should not be attempted')
    monkeypatch.setattr(cli, '_login', unexpected)
    with pytest.raises(SystemExit) as exc:
        cli.main(['ntp', '--no-env-file', *flags])
    assert exc.value.code == 2


def test_invalid_environment_can_be_overridden(monkeypatch):
    monkeypatch.setenv('NETBOX_AUTOFILTER', 'typo')
    with pytest.raises(NetBoxError, match='true or false'):
        settings_from(SimpleNamespace())
    assert not settings_from(SimpleNamespace(netbox_autofilter=False))['autofilter']


def test_rollback_rejects_explicit_netbox_autofilter_before_login(monkeypatch):
    def unexpected(*args): raise AssertionError('login should not be attempted')
    monkeypatch.setattr(cli, '_login', unexpected)
    with pytest.raises(SystemExit) as exc:
        cli.main(['rollback', '--no-env-file', '--netbox', '--netbox-autofilter', '--poller', 'boston'])
    assert exc.value.code == 2


def test_cli_explicit_enable_disable_and_local_limit(device, login, netbox, monkeypatch, tmp_path):
    topology = Topology()
    topology.devices = netbox['devices']
    for row in topology.devices:
        row.update(site={'id': 13}, tags=[])
    topology.devices[0]['tags'] = tags('boston')
    monkeypatch.setattr('netops.netbox.Client', lambda *a, **kw: topology)
    monkeypatch.setenv('NETOPS_INVENTORY', 'netbox')
    monkeypatch.setenv('NETOPS_POLLER', 'dallas')
    monkeypatch.setenv('NETBOX_AUTOFILTER', 'false')
    report = tmp_path / 'explicit.json'
    # CLI overrides both environment settings; --limit cannot bring back a
    # device rejected by ownership. No device commands execute on no matches.
    assert cli.main(['ntp', '--no-env-file', '--netbox-autofilter', '--poller', 'boston',
                     '--limit', 'leaf1', '--report', str(report)]) == cli.EXIT_USAGE
    assert device['commands'] == device['config'] == {}
    doc = json.loads(report.read_text())
    assert doc['inventory_selection']['poller_tag'] == 'poller-boston'
    assert doc['inventory_selection']['selected'] == 1 and not doc['devices']
    # An explicit disable wins over .env without requiring a poller identity.
    monkeypatch.setenv('NETBOX_AUTOFILTER', 'true')
    monkeypatch.delenv('NETOPS_POLLER')
    topology.calls.clear()
    assert cli.main(['ntp', '--no-env-file', '--no-netbox-autofilter', '--report', str(report)]) == cli.EXIT_OK
    doc = json.loads(report.read_text())
    assert set(doc['devices']) == {'sw1', 'sw2', 'leaf1'}
    assert 'inventory_selection' not in doc
    assert not any(path in ('dcim/regions/', 'dcim/sites/', 'ipam/prefixes/') for path, params in topology.calls)


@pytest.mark.parametrize('failure', ['no-matches', 'unreadable'])
def test_cli_selection_failure_is_archived_without_device_commands(device, login, netbox, monkeypatch, tmp_path, failure):
    topology = Topology()
    topology.devices = netbox['devices']
    for row in topology.devices:
        row.update(site={'id': 13}, tags=[])
    original = topology.get
    def get(path, params=None):
        if failure == 'unreadable' and path == 'dcim/regions/':
            raise NetBoxError('NetBox ownership read failed')
        return original(path, params)
    topology.get = get
    monkeypatch.setattr('netops.netbox.Client', lambda *a, **kw: topology)
    report = tmp_path / 'failed.json'
    assert cli.main(['ntp', '--no-env-file', '--netbox', '--netbox-autofilter', '--poller', 'unknown',
                     '--apply', '--yes', '--report', str(report)]) == cli.EXIT_USAGE
    doc = json.loads(report.read_text())
    assert doc['status'] == 'failed' and not doc['devices']
    assert doc['inventory_selection']['status'] == ('resolved' if failure == 'no-matches' else 'resolving')
    assert device['commands'] == device['config'] == {}
