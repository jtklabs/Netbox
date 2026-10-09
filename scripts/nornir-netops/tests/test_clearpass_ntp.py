import json
import shlex
from types import SimpleNamespace

import pytest

from netops import clearpass_ntp as cppm, cli, netbox
from netops.core import MODE_ADD, MODE_REPLACE
from test_waf import setup as waf_setup

CLUSTER = '''Cluster Communication Mode: ipv4
Cluster high-availability : DISABLED
+-----------+-----------+---------+-----------+---------------------------+--------------------+---------+
| MGMT IP | MGMT IPV6 | DATA IP | DATA IPV6 | TYPE | REP_LAST_UPDATE_TS | STATUS |
+-----------+-----------+---------+-----------+---------------------------+--------------------+---------+
| 192.0.2.1 | | | | Publisher [local machine] | | ENABLED |
| 192.0.2.2 | | | | Subscriber | 2026-10-07 01:00:00 | ENABLED |
+-----------+-----------+---------+-----------+---------------------------+--------------------+---------+
REP_LAST_UPDATE_TS - Last timestamp when Replication delay was updated
'''
LEGACY = '''Cluster Commuication Mode: ipv4
Cluster high-availability : ENABLED, Failover wait-time : 8, Standby Publisher : 192.0.2.2
Publisher : Management port IP=192.0.2.1 IPv6= Data port IP= [local machine]
Subscriber : Management port IP=192.0.2.2 IPv6= Data port IP=
'''


def ntp(servers, authenticated=False):
    lines = ['===========================================', 'NTP Server Information', '-------------------------------------------']
    for i, server in enumerate(servers):
        lines.append(('Primary' if i == 0 else 'Secondary') + ' NTP : ' + server)
        if authenticated:
            lines += ['Key ID: 24', 'Algorithm: SHA1']
    if not servers:
        lines.append('Primary NTP : <not configured>')
    if len(servers) < 2:
        lines.append('Secondary NTP : <not configured>')
    return '\n'.join(lines + ['==========================================='])


class Appliance:
    def __init__(self):
        self.output = ntp(['192.0.2.9'])
        self.cluster = CLUSTER
        self.reads, self.writes = [], []
        self.ignore = self.reject = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def command(self, command):
        if command in (cppm.SHOW, cppm.CLUSTER):
            self.reads.append(command)
            return self.output if command == cppm.SHOW else self.cluster
        self.writes.append(command)
        assert command.startswith('configure date -p ')
        if self.reject:
            raise ValueError('device rejected command')
        if not self.ignore:
            self.output = ntp(shlex.split(command)[3::2])
        return 'Configuration updated'


@pytest.fixture
def setup(waf_setup, monkeypatch):
    box = Appliance()
    monkeypatch.setattr(cppm, 'Client', lambda host: box)
    standard = {'ntp': {'servers': ['192.0.2.10', '192.0.2.11'],
                        'clearpass': {'cluster_members': ['192.0.2.1', '192.0.2.2']}}}
    waf_setup.standards.write_text(json.dumps(standard))
    waf_setup.csv.write_text('host,name,platform\n192.0.2.1,cppm,aruba_clearpass\n')
    nb = waf_setup.nb
    nb.device.update(name='cppm', platform={'slug': 'aruba-clearpass'}, status={'value': 'active'},
                     tags=[{'slug': 'clearpass-publisher'}])
    subscriber = {**nb.device, 'id': 8, 'name': 'subscriber',
                  'primary_ip4': {'address': '192.0.2.2/24'}, 'tags': [{'slug': 'clearpass-subscriber'}]}
    members = [nb.device, subscriber]
    real_get = netbox.Client

    def client(*args, **kwargs):
        obj = real_get(*args, **kwargs)
        original_get = obj.get

        def get(path, params=None):
            if path == 'dcim/devices/' and params and params.get('tag') in (
                    'clearpass-publisher', 'clearpass-subscriber'):
                return [d for d in members if params['tag'] in {t['slug'] for t in d['tags']}]
            if path in ('dcim/interfaces/', 'extras/tags/'):
                return []
            return original_get(path, params)
        obj.get = get
        return obj

    monkeypatch.setattr(netbox, 'Client', client)
    original = waf_setup.run

    def run(*args, approval=True, inventory=True):
        if approval:
            args += ('--allow-clearpass-cluster-changes',)
        return original(*args, feature='ntp', netbox_inventory=inventory)
    return SimpleNamespace(box=box, standard=standard, file=waf_setup.standards,
                           members=members, run=run)


def test_readonly_audit_and_scope_reporting(setup):
    code, report = setup.run('--replace', '--fail-on-diff')
    assert code == cli.EXIT_DIFF
    row = report['devices']['cppm']
    assert row['current'] == ['192.0.2.9']
    assert row['affected_members'] == ['192.0.2.1', '192.0.2.2']
    assert row['commands'] == ['configure date -p 192.0.2.10 -s 192.0.2.11']
    assert row['backout']['steps'][0]['command'] == 'configure date -p 192.0.2.9'
    assert setup.box.writes == []


@pytest.mark.parametrize('replace', [True, False])
def test_apply_readback_and_idempotence(setup, replace):
    flags = ['--apply'] + (['--replace'] if replace else [])
    code, report = setup.run(*flags)
    assert code == cli.EXIT_OK
    row = report['devices']['cppm']
    assert row['saved'] and row['verified']
    assert len(row['config_after']) == (2 if replace else 3)
    assert setup.run(*flags)[1]['devices']['cppm']['status'] == 'ok'
    assert len(setup.box.writes) == 1
    assert '-z ' not in setup.box.writes[0]


def test_subscriber_audit_reports_attention_and_apply_is_blocked(setup):
    setup.members[0]['tags'] = [{'slug': 'clearpass-subscriber'}]
    setup.members[1]['tags'] = [{'slug': 'clearpass-publisher'}]
    setup.box.cluster = CLUSTER.replace('Publisher [local machine]', 'Subscriber [local machine]').replace(
        '| Subscriber |', '| Publisher |')
    code, report = setup.run('--fail-on-diff')
    assert code == cli.EXIT_DIFF
    assert report['devices']['cppm']['status'] == 'attention'
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert not setup.box.writes


def test_cluster_tags_and_explicit_approval_are_required(setup):
    assert setup.run('--apply', approval=False)[0] == cli.EXIT_FAILED
    assert setup.run('--apply', inventory=False)[0] == cli.EXIT_FAILED
    setup.members.pop()
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert not setup.box.writes


def test_legacy_yaml_membership_is_ignored(setup):
    setup.standard['ntp']['clearpass']['cluster_members'] = ['192.0.2.99']
    setup.file.write_text(json.dumps(setup.standard))
    assert setup.run('--apply')[0] == cli.EXIT_OK


def test_pinned_membership_change_blocks_before_ssh(setup, tmp_path):
    pinned = tmp_path / 'cluster.json'
    pinned.write_text(json.dumps([{'device_id': 7, 'address': '192.0.2.1', 'role': 'publisher'}]))
    assert setup.run('--apply', '--clearpass-cluster-snapshot', str(pinned))[0] == cli.EXIT_FAILED
    assert not setup.box.reads and not setup.box.writes


def test_authenticated_existing_config_is_not_overwritten(setup):
    setup.box.output = ntp(['192.0.2.9'], authenticated=True)
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert not setup.box.writes


@pytest.mark.parametrize('flags', [('--vrf', 'MGMT'),
                                  ('--prefer', '192.0.2.10'), ('--no-save',), ('--no-verify',),
                                  ('--servers', '2001:db8::1'), ('--servers', 'ntp.example.net')])
def test_unsupported_options_fail_without_writes(setup, flags):
    assert setup.run('--apply', *flags)[0] == cli.EXIT_FAILED
    assert not setup.box.writes


def test_csv_source_override_is_rejected(setup):
    assert setup.run('--source', 'Loopback0', inventory=False)[0] == cli.EXIT_FAILED
    assert not setup.box.reads


@pytest.mark.parametrize('failure', ['ignore', 'reject'])
def test_failed_apply_preserves_before_state_and_is_not_compliant(setup, failure):
    setattr(setup.box, failure, True)
    code, report = setup.run('--apply')
    assert code == cli.EXIT_FAILED
    row = report['devices']['cppm']
    assert row['config_before'][0]['server'] == '192.0.2.9'
    assert row['verified'] is not True
    assert not row['saved']
    assert 'cluster change was attempted' in row['advisories'][-1]


def test_cluster_changes_during_planning_stop_apply(setup, monkeypatch):
    original = setup.box.command
    reads = 0

    def changed(command):
        nonlocal reads
        if command == cppm.CLUSTER:
            reads += 1
            if reads > 1:
                return CLUSTER.replace('192.0.2.2', '192.0.2.3')
        return original(command)

    monkeypatch.setattr(setup.box, 'command', changed)
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert not setup.box.writes


@pytest.mark.parametrize('value', ['', 'ERROR: unavailable', 'NTP Server Information\nPrimary NTP : 192.0.2.1',
                                  ntp(['192.0.2.1', '192.0.2.1']), ntp(['2001:db8::1']),
                                  ntp(['192.0.2.1']) + '\nUnexpected servers: 192.0.2.2'])
def test_incomplete_ntp_output_is_not_empty_configuration(value):
    with pytest.raises(ValueError):
        cppm.parse_ntp(value)


def test_unconfigured_state_and_authentication_fields():
    assert cppm.parse_ntp(ntp([])) == []
    rows = cppm.parse_ntp(ntp(['192.0.2.1', '192.0.2.2'], authenticated=True))
    assert rows[1] == {'server': '192.0.2.2', 'role': 'secondary', 'key_id': '24', 'algorithm': 'SHA1'}


@pytest.mark.parametrize('output', [CLUSTER, LEGACY])
def test_both_documented_cluster_formats(output):
    rows = cppm.parse_cluster(output, '192.0.2.1')
    assert [row['address'] for row in rows] == ['192.0.2.1', '192.0.2.2']
    assert rows[0]['local'] and rows[0]['role'] == 'publisher'


@pytest.mark.parametrize('output', [CLUSTER.replace('ipv4', 'ipv6'), CLUSTER.replace('192.0.2.1', '192.0.2.3'),
                                  CLUSTER.replace('Subscriber', 'Publisher'), CLUSTER.replace('[local machine]', ''),
                                  CLUSTER + '\nSubscriber bad output', CLUSTER.replace('| STATUS |', '| bogus |')])
def test_unknown_or_ambiguous_cluster_is_rejected(output):
    with pytest.raises(ValueError):
        cppm.parse_cluster(output, '192.0.2.1')


def test_ntp_order_is_not_a_preference():
    rows = cppm.parse_ntp(ntp(['192.0.2.1', '192.0.2.2']))
    assert cppm.plan(rows, ['192.0.2.2', '192.0.2.1'], MODE_REPLACE)[1] is False


@pytest.mark.parametrize('mode', [MODE_ADD, MODE_REPLACE])
def test_server_limit(mode):
    with pytest.raises(ValueError, match='five'):
        cppm.plan([], [f'192.0.2.{n}' for n in range(1, 7)], mode)


@pytest.mark.parametrize('value', [{'password': 'secret'}, {'cluster_members': '192.0.2.1'},
                                  {'cluster_members': ['192.0.2.1', '192.0.2.1']},
                                  {'cluster_members': ['2001:db8::1']}, []])
def test_clearpass_settings_validation(value):
    with pytest.raises(ValueError):
        cppm.settings(value)


def test_transport_uses_readonly_login_known_hosts_and_exact_prompt(monkeypatch):
    import netmiko.terminal_server

    calls, connects = [], []
    class Shell:
        def __init__(self, **kwargs):
            connects.append(kwargs)

        def find_prompt(self):
            return '[appadmin@publisher]#'

        def send_command(self, command, **kwargs):
            calls.append((command, kwargs))
            return ntp(['192.0.2.10'])

        def disconnect(self):
            calls.append(('disconnect', {}))

    monkeypatch.setattr(netmiko.terminal_server, 'TerminalServerSSH', Shell)
    params = SimpleNamespace(hostname='192.0.2.1', username='appadmin', password='secret', port=22, extras={})
    host = SimpleNamespace(get_connection_parameters=lambda name: params)
    with cppm.Client(host) as client:
        assert cppm.parse_ntp(client.command(cppm.SHOW))[0]['server'] == '192.0.2.10'
    assert [command for command, _ in calls] == ['show ntp', 'disconnect']
    assert connects[0]['ssh_strict'] and connects[0]['system_host_keys']
    assert calls[0][1]['expect_string'] == r'\[appadmin@publisher\]\#\s*$'
