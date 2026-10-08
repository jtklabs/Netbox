import copy
import json
from types import SimpleNamespace

import pytest

from netops import aruba_ntp as aruba, cli, netbox
from netops.core import MODE_REPLACE
from test_waf import setup as waf_setup

MAC = '00:11:22:33:44:55'
PATH = '/md/campus/' + MAC


class Conductor:
    def __init__(self):
        self.path = PATH
        self.config = {aruba.SERVERS: [{'ip': '192.0.2.9', 'iburst': True}], aruba.SOURCE: None}
        self.info = {'_global': {'_switch_role': 'conductor'},
                     '_local': {'_type': 'device', '_hardware': {'_mac': MAC},
                                '_pending': {'write_mem_reqd': False}}}
        self.switch = {'IP Address': '192.0.2.1', 'MAC': MAC, 'Type': 'MD',
                       'Nodepath': '/md/campus',
                       'Config ID': '1', 'Status': 'up', 'Configuration State': 'UPDATE SUCCESSFUL'}
        self.writes = []
        self.ignore_commit = self.fail_commit = False
        self.staged = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def switches(self, debug=False):
        return [copy.deepcopy(self.switch)]

    def get(self, name, path, **params):
        assert path == self.path
        if name == 'sys_info':
            return copy.deepcopy(self.info)
        assert params == {'type': 'committed', 'limit': 65536, 'offset': 1}
        return {'_data': {name: copy.deepcopy(self.config[name])}}

    def post(self, name, path, body):
        assert path == self.path
        self.writes.append((name, copy.deepcopy(body)))
        if self.staged is None:
            self.staged = copy.deepcopy(self.config)
        if name == 'write_memory':
            if self.fail_commit:
                raise RuntimeError('commit failed')
            if not self.ignore_commit:
                self.config = self.staged
                self.staged = None
                self.switch['Config ID'] = str(int(self.switch['Config ID']) + 1)
            return
        body = copy.deepcopy(body)
        action = body.pop('_action')
        if name == aruba.SERVERS:
            rows = self.staged[name]
            existing = next((row for row in rows if row['ip'] == body['ip']), None)
            if action == 'delete':
                rows.remove(existing)
            elif existing:
                existing.update(body)
            else:
                rows.append(body)
        else:
            self.staged[name] = body


@pytest.fixture
def setup(waf_setup, monkeypatch):
    box = Conductor()
    monkeypatch.setattr(aruba, 'Client', lambda *args: box)
    # No real waiting for deployment-timeout cases.
    ticks = iter(range(0, 1000, 40))
    monkeypatch.setattr(aruba.time, 'monotonic', lambda: next(ticks))
    standard = {'ntp': {'servers': ['192.0.2.10'], 'aruba': {
        'conductor': '192.0.2.254', 'exclusive_change': True}}}
    waf_setup.standards.write_text(json.dumps(standard))
    waf_setup.csv.write_text('host,name,platform,aruba_config_path\n192.0.2.1,wlc,aruba_os,' + PATH + '\n')
    original = waf_setup.run
    return SimpleNamespace(box=box, standard=standard, file=waf_setup.standards,
                           run=lambda *args: original(*args, feature='ntp'))


@pytest.fixture
def tagged(setup, waf_setup, monkeypatch):
    from test_netbox import device
    conductor = device(id=8, name='conductor', platform='arubaos', address='192.0.2.254/24',
                       tags=[{'slug': 'mobility-conductor'}])
    waf_setup.nb.device.update(name='wlc', platform={'slug': 'arubaos'}, status={'value': 'active'},
                              custom_fields={'aruba_conductor': 'ignored.example', 'aruba_config_path': '/wrong'})
    setup.standard['ntp']['aruba'].pop('conductor')
    setup.file.write_text(json.dumps(setup.standard))
    tagged_devices, queries, endpoints = [conductor], [], []
    selected_devices = [waf_setup.nb.device]
    appliances = {'192.0.2.1': setup.box}
    original_client = netbox.Client

    def client(*args, **kwargs):
        obj = original_client(*args, **kwargs)
        original_get = obj.get

        def get(path, params=None):
            queries.append((path, params))
            if path == 'dcim/devices/' and params == {'tag': 'mobility-conductor'}:
                return tagged_devices
            if path == 'dcim/devices/':
                return selected_devices
            if path == 'dcim/interfaces/':
                return []
            return original_get(path, params)
        obj.get = get
        return obj

    def connect(host, address, options):
        endpoints.append(address)
        return appliances[host.hostname]

    monkeypatch.setattr(netbox, 'Client', client)
    monkeypatch.setattr(aruba, 'Client', connect)
    setup.run = lambda *args: waf_setup.run(*args, feature='ntp', netbox_inventory=True)
    setup.tagged_devices, setup.queries, setup.endpoints = tagged_devices, queries, endpoints
    setup.selected_devices, setup.appliances = selected_devices, appliances
    return setup


def test_each_selected_wlc_gets_its_own_audit_result(tagged):
    other = Conductor()
    other.switch['IP Address'] = '192.0.2.2'
    other.switch['MAC'] = '00:11:22:33:44:66'
    other.info['_local']['_hardware']['_mac'] = other.switch['MAC']
    other.path = '/md/campus/' + other.switch['MAC']
    switches = lambda debug=False: [copy.deepcopy(tagged.box.switch), copy.deepcopy(other.switch)]
    tagged.box.switches = other.switches = switches
    other.config[aruba.SERVERS] = [{'ip': '192.0.2.10', 'iburst': True}]
    tagged.selected_devices.append({**tagged.selected_devices[0], 'id': 9, 'name': 'wlc2',
                                   'primary_ip4': {'address': '192.0.2.2/24'}})
    tagged.appliances['192.0.2.2'] = other
    code, report = tagged.run('--fail-on-diff')
    assert code == cli.EXIT_DIFF
    assert set(report['devices']) == {'wlc', 'wlc2'}
    assert not report['devices']['wlc']['compliant']
    assert report['devices']['wlc2']['compliant']
    assert report['devices']['wlc']['config_path'] == PATH
    assert report['devices']['wlc2']['config_path'] == other.path
    assert tagged.box.writes == other.writes == []
    assert tagged.endpoints == ['192.0.2.254', '192.0.2.254']


def test_tagged_conductor_audit_and_apply_need_no_manual_fields(tagged):
    code, report = tagged.run('--fail-on-diff', '--netbox-filter', 'id=7')
    assert code == cli.EXIT_DIFF
    assert tagged.box.writes == []
    row = report['devices']['wlc']
    assert row['config_path'] == PATH
    assert row['conductor_inventory'] == {'device_id': 8, 'address': '192.0.2.254'}
    assert ('dcim/devices/', {'tag': 'mobility-conductor'}) in tagged.queries
    assert tagged.run('--apply')[0] == cli.EXIT_OK
    assert tagged.endpoints == ['192.0.2.254', '192.0.2.254']


@pytest.mark.parametrize('failure', ['missing', 'multiple', 'inactive', 'no_ip', 'unfiltered', 'self'])
def test_bad_conductor_tags_block_before_connect(tagged, failure):
    row = tagged.tagged_devices[0]
    if failure == 'missing':
        tagged.tagged_devices.clear()
    elif failure == 'multiple':
        tagged.tagged_devices.append(copy.deepcopy(row))
    elif failure == 'inactive':
        row['status'] = {'value': 'offline'}
    elif failure == 'no_ip':
        row['primary_ip4'] = None
    elif failure == 'unfiltered':
        row['tags'] = []
    else:
        row['id'] = 7
    assert tagged.run('--apply')[0] == cli.EXIT_FAILED
    assert tagged.endpoints == [] and not tagged.box.writes


def test_pinned_conductor_change_blocks_before_connect(tagged, tmp_path):
    snapshot = tmp_path / 'conductor.json'
    snapshot.write_text(json.dumps({'device_id': 8, 'address': '192.0.2.253'}))
    assert tagged.run('--apply', '--mobility-conductor-snapshot', str(snapshot))[0] == cli.EXIT_FAILED
    assert tagged.endpoints == []


@pytest.mark.parametrize('node', ['/md/campus', PATH, '/md'])
def test_discovers_device_leaf_from_parent_or_full_path(node):
    client = Conductor()
    client.switch['Nodepath'] = node
    assert aruba.discover_path(client, '192.0.2.1') == (node if node.endswith(MAC) else node + '/' + MAC)


@pytest.mark.parametrize('failure', ['missing_path', 'root', 'wrong_leaf', 'traversal', 'no_mac',
                                     'not_wlc', 'duplicate_ip', 'duplicate_mac', 'missing_wlc'])
def test_ambiguous_or_invalid_wlc_discovery_is_rejected(tagged, monkeypatch, failure):
    row = tagged.box.switch
    if failure == 'missing_path':
        row.pop('Nodepath')
    elif failure == 'root':
        row['Nodepath'] = '/mm'
    elif failure == 'wrong_leaf':
        row['Nodepath'] = '/md/00:00:00:00:00:00'
    elif failure == 'traversal':
        row['Nodepath'] = '/md/../campus'
    elif failure == 'no_mac':
        row.pop('MAC')
    elif failure == 'not_wlc':
        row['Type'] = 'conductor'
    elif failure == 'missing_wlc':
        row['IP Address'] = '192.0.2.2'
    else:
        other = {**row, 'IP Address': '192.0.2.2'} if failure == 'duplicate_mac' else dict(row)
        monkeypatch.setattr(tagged.box, 'switches', lambda **kwargs: [row, other])
    assert tagged.run('--apply')[0] == cli.EXIT_FAILED
    assert not tagged.box.writes


def test_wlc_moved_during_audit_cannot_be_modified(tagged, monkeypatch):
    original = tagged.box.get

    def get(name, path, **params):
        result = original(name, path, **params)
        if name == aruba.SOURCE:
            tagged.box.switch['Nodepath'] = '/md/other'
        return result

    monkeypatch.setattr(tagged.box, 'get', get)
    assert tagged.run('--apply')[0] == cli.EXIT_FAILED
    assert not tagged.box.writes


def test_audit_is_read_only_and_reports_drift(setup):
    code, report = setup.run('--replace', '--fail-on-diff')
    assert code == cli.EXIT_DIFF
    row = report['devices']['wlc']
    assert row['current'] == ['192.0.2.9']
    assert row['desired'] == ['192.0.2.10']
    assert row['config_path'] == PATH
    assert setup.box.writes == []
    steps = row['implementation']['steps']
    assert all(step['transport'] == 'rest' for step in steps)
    assert next(i for i, step in enumerate(steps) if step['purpose'] == 'persist_config') < next(
        i for i, step in enumerate(steps) if step['purpose'] == 'verify_config')


@pytest.mark.parametrize('replace', [True, False])
def test_apply_and_idempotence(setup, replace):
    flags = ['--apply'] + (['--replace'] if replace else [])
    code, report = setup.run(*flags)
    assert code == cli.EXIT_OK
    assert report['devices']['wlc']['verified'] is True
    assert report['devices']['wlc']['saved'] is True
    assert len(setup.box.config[aruba.SERVERS]) == (1 if replace else 2)
    count = len(setup.box.writes)
    assert setup.run(*flags)[1]['devices']['wlc']['status'] == 'ok'
    assert len(setup.box.writes) == count


@pytest.mark.parametrize('value,body', [('Vlan25', {'vlanid': 25}), ('Loopback0', {'loopback': True})])
def test_source_configuration(setup, value, body):
    assert setup.run('--apply', '--source', value)[0] == cli.EXIT_OK
    assert setup.box.config[aruba.SOURCE] == body


@pytest.mark.parametrize('failure', ['pending', 'wrong_mac', 'group', 'offline', 'unsynced',
                                     'no_pending_state', 'not_conductor', 'exclusive'])
def test_safety_guards_prevent_writes(setup, failure):
    if failure == 'pending':
        setup.box.info['_local']['_pending']['write_mem_reqd'] = True
    elif failure == 'wrong_mac':
        setup.box.switch['MAC'] = '00:00:00:00:00:00'
    elif failure == 'group':
        setup.box.info['_local']['_type'] = 'group'
    elif failure == 'offline':
        setup.box.switch['Status'] = 'down'
    elif failure == 'unsynced':
        setup.box.switch['Configuration State'] = 'CONFIG ROLLBACK'
    elif failure == 'no_pending_state':
        setup.box.info['_local']['_pending'] = {}
    elif failure == 'not_conductor':
        setup.box.info['_global']['_switch_role'] = 'MD'
    else:
        setup.standard['ntp']['aruba']['exclusive_change'] = False
        setup.file.write_text(json.dumps(setup.standard))
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert not setup.box.writes


@pytest.mark.parametrize('flags', [('--no-save',), ('--no-verify',), ('--servers', '2001:db8::1'),
                                  ('--servers', 'ntp.example.com'), ('--source', 'GigabitEthernet0/0'),
                                  ('--vrf', 'MGMT'), ('--prefer', '192.0.2.10')])
def test_unsupported_options_prevent_writes(setup, flags):
    assert setup.run('--apply', *flags)[0] == cli.EXIT_FAILED
    assert not setup.box.writes


def test_inherited_drift_reported_and_never_modified(setup):
    setup.box.config[aruba.SERVERS][0]['_flags'] = {'inherited': True}
    code, report = setup.run('--replace', '--fail-on-diff')
    assert code == cli.EXIT_DIFF
    assert report['devices']['wlc']['status'] == 'attention'
    assert 'inherited' in report['devices']['wlc']['advisories'][0]
    assert setup.run('--replace', '--apply')[0] == cli.EXIT_FAILED
    assert not setup.box.writes


def test_inherited_matching_configuration_is_compliant(setup):
    setup.box.config[aruba.SERVERS] = [{'ip': '192.0.2.10', 'iburst': True, '_flags': {'inherited': True}}]
    assert setup.run('--replace', '--fail-on-diff')[1]['devices']['wlc']['status'] == 'ok'
    assert not setup.box.writes


@pytest.mark.parametrize('failure', ['ignore_commit', 'fail_commit'])
def test_commit_failures_never_report_success(setup, failure):
    setattr(setup.box, failure, True)
    code, report = setup.run('--apply')
    assert code == cli.EXIT_FAILED
    assert not report['devices']['wlc']['verified']
    assert 'no automatic discard' in report['devices']['wlc']['advisories'][-1]


def test_modify_preserves_auth_binding_and_unselected_source(setup):
    setup.box.config[aruba.SERVERS] = [{'ip': '192.0.2.10', 'iburst': False, 'keyid': 3}]
    setup.box.config[aruba.SOURCE] = {'vlanid': 40}
    assert setup.run('--apply')[0] == cli.EXIT_OK
    assert setup.box.config[aruba.SERVERS][0]['keyid'] == 3
    assert setup.box.config[aruba.SOURCE] == {'vlanid': 40}


def test_concurrent_change_during_audit_is_not_compliant(setup, monkeypatch):
    setup.box.config[aruba.SERVERS] = [{'ip': '192.0.2.10', 'iburst': True}]
    original = setup.box.get

    def get(name, path, **params):
        result = original(name, path, **params)
        if name == aruba.SOURCE:
            setup.box.switch['Config ID'] = '2'
        return result

    monkeypatch.setattr(setup.box, 'get', get)
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert not setup.box.writes


@pytest.mark.parametrize('value', [None, {}, 'bad', [{'ip': '2001:db8::1'}],
                                  [{'ip': '192.0.2.10', 'iburst': 'false'}]])
def test_malformed_config_never_becomes_empty_compliant_config(setup, value):
    setup.box.config[aruba.SERVERS] = value
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert not setup.box.writes


@pytest.mark.parametrize('path', ['/md', '/md/campus', '/mm/' + MAC, '/md/../' + MAC, None])
def test_group_and_invalid_paths_fail_before_any_api_call(path):
    with pytest.raises(ValueError, match='aruba_config_path'):
        aruba.identity(None, path, '192.0.2.1')


@pytest.mark.parametrize('body', [{}, {'_global_result': {'status': 1}},
                                  {'_global_result': {'status': 0}, 'ntp': {'_result': {'status': 1}}},
                                  {'Error': 'internal failure'}])
def test_http_200_is_not_sufficient(body):
    with pytest.raises((RuntimeError, ValueError)):
        aruba.checked(body, require_result=True)


@pytest.mark.parametrize('value', ['Vlan0', 'Vlan4096', 'Loopback1', 'Management0'])
def test_invalid_source(value):
    with pytest.raises(ValueError):
        aruba.source(value)


def test_server_limit():
    before = {aruba.SERVERS: [], aruba.SOURCE: None}
    with pytest.raises(ValueError, match='16'):
        aruba.plan(before, {'servers': [f'192.0.2.{n}' for n in range(1, 18)], 'iburst': True}, MODE_REPLACE)


def test_client_authentication_tls_and_redacted_transport_errors(monkeypatch):
    import requests

    calls = []
    class Session:
        headers = {}

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith('/login'):
                return SimpleNamespace(status_code=200, json=lambda: {'_global_result': {
                    'status': '0', 'UIDARUBA': 'session-secret', 'X-CSRFToken': 'csrf-secret'}})
            if url.endswith('/logout'):
                return SimpleNamespace(status_code=200, json=lambda: {})
            raise requests.ConnectionError('Sensitive URL?UIDARUBA=session-secret')

        def close(self):
            pass

    session = Session()
    monkeypatch.setattr(requests, 'Session', lambda: session)
    with aruba.Client(SimpleNamespace(username='admin', password='password'), '192.0.2.254', aruba.settings({})) as client:
        with pytest.raises(RuntimeError) as exc:
            client.get(aruba.SERVERS, PATH)
        assert 'secret' not in str(exc.value)
        assert session.headers['X-CSRF-Token'] == 'csrf-secret'
    assert calls[0][2]['data'] == {'username': 'admin', 'password': 'password'}
    assert all(call[2]['allow_redirects'] is False and call[2]['verify'] is True for call in calls)
    assert all('UIDARUBA' not in call[2]['params'] for call in calls)
