import copy
import json
import shlex
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from nornir.core.inventory import Host

from netops import cli, junos_ntp as junos, ntp_discovery, syslog_discovery
from netops.core import MODE_ADD, MODE_REPLACE, canonical_platform
from netops.features.ntp import FEATURE
from test_ntp import parse_args
from test_waf import setup as waf_setup


def config(body):
    return '<rpc-reply xmlns="http://xml.juniper.net/junos/23.4R1/junos"><configuration><system><ntp>' + body + '</ntp></system></configuration></rpc-reply>'


def interfaces(name='lo0.0', addresses=('192.0.2.254',)):
    return ('<rpc-reply><interface-information><physical-interface><logical-interface><name>' + name +
            '</name><address-family><address-family-name>inet</address-family-name>' +
            ''.join('<interface-address><ifa-local>' + value + '</ifa-local></interface-address>' for value in addresses) +
            '</address-family></logical-interface></physical-interface></interface-information></rpc-reply>')


def desired(*flags):
    return FEATURE.build_desired(parse_args(['-s', '192.0.2.10', *flags])).variables


class Junos:
    def __init__(self):
        self.ntp = ET.fromstring('<ntp><server><name>192.0.2.9</name><version>4</version></server></ntp>')
        self.candidate = None
        self.calls = []
        self.failure = None
        self.interface_xml = interfaces()
        self.private = False

    def send_command(self, command, **kwargs):
        self.calls.append(command)
        if command.removeprefix('run ') == junos.SHOW:
            if self.failure == 'read':
                return '<rpc-reply><rpc-error><error-message>denied</error-message></rpc-error></rpc-reply>'
            return config(''.join(ET.tostring(child, encoding='unicode') for child in self.ntp))
        if command.removeprefix('run ') == junos.INTERFACES:
            return self.interface_xml
        if command == 'show | compare':
            return '[edit system]\n+ host-name other;' if self.failure == 'dirty' else ''
        if command == 'run show system commit':
            return '0 now by admin via cli rollback in 4mins' if self.failure == 'pending' else '0 now by admin via cli'
        if command == 'rollback 0':
            self.candidate = copy.deepcopy(self.ntp)
            return 'load complete'
        raise AssertionError(command)

    def config_mode(self, config_command):
        self.calls.append(config_command)
        assert config_command == 'configure private'
        if self.failure == 'lock':
            raise ValueError('configuration database locked')
        self.private = True
        self.candidate = copy.deepcopy(self.ntp)
        return 'Entering configuration mode'

    def send_config_set(self, commands, **kwargs):
        assert self.private and kwargs == {'exit_config_mode': False}
        self.calls.extend(commands)
        if self.failure == 'write':
            return 'error: rejected'
        for command in commands:
            action, system, ntp, kind, name, *tail = shlex.split(command)
            assert (system, ntp) == ('system', 'ntp')
            row = next((row for row in self.candidate.findall(kind)
                        if (row.text if kind == 'trusted-key' else row.findtext('name')) == name), None)
            if action == 'delete':
                if tail:
                    row.remove(row.find(tail[0]))
                else:
                    self.candidate.remove(row)
                continue
            if row is None:
                row = ET.SubElement(self.candidate, kind)
                if kind == 'trusted-key':
                    row.text = name
                else:
                    ET.SubElement(row, 'name').text = name
            while tail:
                key = tail.pop(0)
                value = None if key == 'prefer' else tail.pop(0)
                node = row.find(key)
                if node is None:
                    node = ET.SubElement(row, key)
                node.text = '$9$encrypted' if key == 'value' else value
        return ''

    def commit(self, **kwargs):
        if kwargs.get('check'):
            self.calls.append('commit check')
            return 'error: commit check failed' if self.failure == 'check' else 'configuration check succeeds'
        if kwargs.get('confirm'):
            assert kwargs['confirm_delay'] == 5
            self.calls.append('commit confirmed 5')
            if self.failure == 'commit':
                raise RuntimeError('commit failed')
            if self.failure != 'verify':
                self.ntp = copy.deepcopy(self.candidate)
            return 'commit confirmed will be automatically rolled back in 5 minutes\ncommit complete'
        self.calls.append('commit')
        return 'error: confirmation failed' if self.failure == 'confirm' else 'commit complete'

    def exit_config_mode(self):
        self.calls.append('exit configuration-mode')
        self.private = False


@pytest.fixture
def setup(waf_setup, monkeypatch):
    box = Junos()
    monkeypatch.setattr(Host, 'get_connection', lambda *args, **kwargs: box)
    standard = {'ntp': {'servers': ['192.0.2.10']}}
    waf_setup.standards.write_text(json.dumps(standard))
    waf_setup.csv.write_text('host,name,platform\n192.0.2.1,srx,juniper_junos\n')
    return SimpleNamespace(box=box, file=waf_setup.standards, standard=standard, csv=waf_setup.csv,
                           run=lambda *args: waf_setup.run(*args, feature='ntp'))


@pytest.mark.parametrize('alias', ['junos', 'juniper_junos', 'juniper_srx', 'juniper_mx', 'srx', 'mx'])
def test_aliases(alias):
    assert canonical_platform(alias) == 'juniper_junos'


def test_audit_only_reads_and_archives_change_plan(setup):
    code, report = setup.run('--replace', '--fail-on-diff')
    assert code == cli.EXIT_DIFF
    row = report['devices']['srx']
    assert row['commands'] == ['set system ntp server 192.0.2.10', 'delete system ntp server 192.0.2.9']
    assert setup.box.calls == [junos.SHOW]
    assert row['current_config']['read_steps'][0]['command'] == junos.SHOW
    steps = [step['command'] for step in row['implementation']['steps']]
    assert steps.index('commit confirmed 5') < steps.index(junos.SHOW) < steps.index('commit')


@pytest.mark.parametrize('platform', ['juniper_srx', 'juniper_mx'])
@pytest.mark.parametrize('replace', [False, True])
def test_apply_and_idempotence(setup, platform, replace):
    setup.csv.write_text(f'host,name,platform\n192.0.2.1,srx,{platform}\n')
    flags = ['--apply'] + (['--replace'] if replace else [])
    code, report = setup.run(*flags)
    assert code == cli.EXIT_OK
    row = report['devices']['srx']
    assert row['saved'] and row['verified'] and row['applied']
    servers = row['config_after']['servers']
    assert set(servers) == ({'192.0.2.10'} if replace else {'192.0.2.10', '192.0.2.9'})
    if not replace:
        assert servers['192.0.2.9']['version'] == '4'
    setup.box.calls.clear()
    code, report = setup.run(*flags)
    assert code == cli.EXIT_OK and report['devices']['srx']['compliant']
    assert setup.box.calls == [junos.SHOW]


@pytest.mark.parametrize('failure', ['read', 'lock', 'dirty', 'pending', 'write', 'check', 'commit', 'verify', 'confirm'])
def test_failed_operations_never_report_success(setup, failure):
    setup.box.failure = failure
    code, report = setup.run('--apply')
    assert code == cli.EXIT_FAILED
    row = report['devices']['srx']
    assert row['verified'] is not True and row['saved'] is not True
    if failure in ('read', 'lock', 'dirty', 'pending'):
        assert not any(call.startswith('set ') for call in setup.box.calls)
        assert 'rollback 0' not in setup.box.calls
    if failure in ('write', 'check'):
        assert 'rollback 0' in setup.box.calls
    if failure in ('commit', 'verify'):
        assert 'commit' not in setup.box.calls and 'rollback 0' not in setup.box.calls


@pytest.mark.parametrize('flag', ['--no-save', '--no-verify'])
def test_apply_requires_commit_and_verification(setup, flag):
    assert setup.run('--apply', flag)[0] == cli.EXIT_FAILED
    assert setup.box.calls == [junos.SHOW]


def test_source_prefer_and_vrf(setup):
    setup.box.interface_xml = interfaces('fxp0.0')
    flags = ['--apply', '--source', 'fxp0.0', '--vrf', 'mgmt_junos', '--prefer', '192.0.2.10']
    code, report = setup.run(*flags)
    assert code == cli.EXIT_OK
    state = report['devices']['srx']['config_after']
    assert state['sources'] == {'mgmt_junos': '192.0.2.254'}
    assert state['servers']['192.0.2.10']['prefer']
    assert state['servers']['192.0.2.10']['vrf'] == 'mgmt_junos'
    assert setup.run(*flags)[1]['devices']['srx']['compliant']


def test_region_and_tag_override_standard(setup, monkeypatch):
    setup.standard['ntp'].update(source='fxp0.0', regions={'east': ['192.0.2.20']})
    setup.file.write_text(json.dumps(setup.standard))
    original = FEATURE.per_device
    # Exercise the same Nornir data fields supplied by NetBox inventory.
    def per_device(keys, variables, host):
        host.data.update(region='east', source_interface={'ntp': 'lo0.0'}, ntp_vrf='default')
        return original(keys, variables, host)
    monkeypatch.setattr('netops.features.ntp.per_device', per_device)
    code, report = setup.run('--apply')
    assert code == cli.EXIT_OK
    row = report['devices']['srx']
    assert row['desired'] == ['192.0.2.20']
    assert row['config_after']['sources'] == {'default': '192.0.2.254'}


@pytest.mark.parametrize('addresses', [(), ('192.0.2.1', '192.0.2.2')])
def test_source_without_exactly_one_address_blocks(setup, addresses):
    setup.box.interface_xml = interfaces(addresses=addresses)
    assert setup.run('--apply', '--source', 'lo0.0')[0] == cli.EXIT_FAILED
    assert not any(call.startswith('set ') for call in setup.box.calls)


def test_authentication_is_redacted_and_idempotent(setup, monkeypatch):
    setup.standard['ntp']['authentication'] = {'key_id': 7, 'type': 'md5'}
    setup.file.write_text(json.dumps(setup.standard))
    monkeypatch.setenv('NETOPS_NTP_KEY_7', 'ntp-test-secret')
    code, report = setup.run('--apply')
    assert code == cli.EXIT_OK
    assert 'ntp-test-secret' not in json.dumps(report) and '$9$encrypted' not in json.dumps(report)
    row = report['devices']['srx']
    assert row['config_after']['keys'] == {'7': 'md5'}
    assert row['config_after']['trusted'] == ['7']
    assert setup.run('--apply')[1]['devices']['srx']['compliant']
    assert setup.run('--apply', '--rewrite-keys')[1]['devices']['srx']['verified']


@pytest.mark.parametrize('attribute', ['group="GLOBAL"', 'inactive="inactive"', 'protect="protect"'])
def test_protected_drift_is_visible_but_never_changed(setup, attribute):
    setup.box.ntp = ET.fromstring(f'<ntp><server {attribute}><name>192.0.2.9</name></server></ntp>')
    code, report = setup.run('--replace', '--fail-on-diff')
    assert code == cli.EXIT_DIFF
    assert report['devices']['srx']['advisories']
    assert setup.run('--replace', '--apply')[0] == cli.EXIT_FAILED
    assert 'configure private' not in setup.box.calls


def test_inherited_compliant_configuration_can_be_audited(setup):
    setup.box.ntp = ET.fromstring('<ntp><server group="GLOBAL"><name>192.0.2.10</name></server></ntp>')
    assert setup.run('--fail-on-diff')[1]['devices']['srx']['compliant']


def test_namespaced_inheritance_marks_configuration_protected():
    before = junos.parse(config('<server xmlns:junos="http://xml.juniper.net/junos/23.4R1/junos" junos:group="GLOBAL"><name>192.0.2.10</name></server>'))
    assert before['protected']
    assert junos.SHOW.endswith('display xml groups')


def test_leaf_updates_preserve_version_and_unmanaged_settings():
    before = junos.parse(config('<server><name>192.0.2.10</name><key>4</key><prefer/><version>3</version><routing-instance>old</routing-instance></server><peer><name>192.0.2.90</name><key>4</key></peer><authentication-key><name>4</name><type>md5</type><value>hidden</value></authentication-key><trusted-key>4</trusted-key>'))
    assert junos.plan(before, desired(), MODE_REPLACE) == [
        'delete system ntp server 192.0.2.10 key', 'delete system ntp server 192.0.2.10 prefer',
        'delete system ntp server 192.0.2.10 routing-instance']


@pytest.mark.parametrize('bad', ['error: denied', '<rpc-reply/>', '<configuration><system>',
                               '<!DOCTYPE foo><configuration/>', config('<server/>'),
                               config('<server><name>2001:db8::1</name></server>')])
def test_invalid_reads_never_become_empty_configuration(bad):
    with pytest.raises(ValueError):
        junos.parse(bad)


@pytest.mark.parametrize('server', ['2001:db8::1', 'ntp.example.com'])
def test_non_ipv4_desired_rejected_before_connect(setup, server):
    assert setup.run('--apply', '--servers', server)[0] == cli.EXIT_FAILED
    assert not setup.box.calls


def test_discovery_resolves_logical_interface_from_configured_ip():
    output = {junos.SHOW: config('<server><name>192.0.2.10</name></server><source-address><name>192.0.2.254</name></source-address>'),
              junos.INTERFACES: interfaces()}
    found = ntp_discovery.observe('junos', output.__getitem__)
    assert (found['status'], found['source'], found['vrf']) == ('resolved', 'lo0.0', 'default')
    assert ntp_discovery.interface_name(found['source']) == 'lo0.0'
    assert 'juniper_junos' not in syslog_discovery.PLATFORMS


def test_discovery_ambiguous_ip_or_vrf_requires_review():
    body = '<server><name>192.0.2.10</name></server><source-address><name>192.0.2.254</name></source-address>'
    output = {junos.SHOW: config(body), junos.INTERFACES: interfaces('fxp0.0', ('192.0.2.1',))}
    assert junos.observe(output.__getitem__)['status'] == 'ambiguous'
    output[junos.SHOW] = config(body + '<server><name>192.0.2.11</name><routing-instance>MGMT</routing-instance></server>')
    assert junos.observe(output.__getitem__)['status'] == 'ambiguous'


@pytest.mark.parametrize('name', ['lo0.0', 'fxp0.0', 'irb.100', 'reth0.10', 'ge-0/0/0.0', 'et-0/0/0:0.0'])
def test_junos_logical_interface_names_round_trip(name):
    assert ntp_discovery.interface_name(name) == name
    assert junos.source_address(name, interfaces(name)) == '192.0.2.254'


def test_collector_discovers_and_bootstraps_tag_without_extra_session(tmp_path):
    from netops import collect
    from test_collect import make_task
    from test_ntp_discovery import Client

    body = '<server><name>192.0.2.10</name></server><source-address><name>192.0.2.254</name></source-address>'
    task, connection = make_task(platform='juniper_junos', replies={junos.SHOW: config(body), junos.INTERFACES: interfaces('irb.100')})
    task.host.data['netbox_id'] = 1
    result = collect.collect_commands(task, collect.load_catalog(), tmp_path)
    observation = result.result['ntp_discovery']
    assert observation['status'] == 'resolved' and observation['source'] == 'irb.100'
    assert 'syslog_discovery' not in result.result
    assert all(command.startswith('show ') for command, _ in connection.sent)
    client = Client()
    client.interfaces[0]['name'] = 'irb.100'
    assert ntp_discovery.sync_device(client, task.host, observation)['status'] == 'written'
    assert client.device['custom_fields']['ntp_vrf'] == 'default'
    assert {tag['slug'] for tag in client.interfaces[0]['tags']} == {'unrelated', 'service-source'}
    assert ntp_discovery.sync_device(client, task.host, observation)['status'] == 'unchanged'


def test_changed_config_after_private_entry_stops_without_writing(setup, monkeypatch):
    original = setup.box.config_mode
    def enter(**kwargs):
        output = original(**kwargs)
        ET.SubElement(ET.SubElement(setup.box.ntp, 'server'), 'name').text = '192.0.2.99'
        return output
    monkeypatch.setattr(setup.box, 'config_mode', enter)
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert not any(call.startswith('set ') for call in setup.box.calls)
    assert 'rollback 0' not in setup.box.calls


def test_missing_tag_leaves_existing_source_unchanged(setup):
    source = ET.SubElement(setup.box.ntp, 'source-address')
    ET.SubElement(source, 'name').text = '192.0.2.200'
    code, report = setup.run('--replace', '--apply')
    assert code == cli.EXIT_OK
    assert report['devices']['srx']['config_after']['sources'] == {'default': '192.0.2.200'}


def test_source_replacement_scoped_to_selected_instance():
    before = junos.parse(config('<source-address><name>192.0.2.1</name></source-address><source-address><name>192.0.2.2</name><routing-instance>MGMT</routing-instance></source-address>'))
    variables = {**desired('--vrf', 'MGMT'), 'source_address': '192.0.2.254'}
    commands = junos.plan(before, variables, MODE_ADD)
    assert 'delete system ntp source-address 192.0.2.2' in commands
    assert 'delete system ntp source-address 192.0.2.1' not in commands
    variables['source_address'] = '192.0.2.1'
    with pytest.raises(ValueError, match='another routing instance'):
        junos.plan(before, variables, MODE_ADD)


@pytest.mark.parametrize('material', ['bad"quoted-secret', 'bad\\escaped-secret'])
def test_unsafe_key_material_rejected_before_connect(setup, monkeypatch, material):
    setup.standard['ntp']['authentication'] = {'key_id': 7, 'type': 'md5'}
    setup.file.write_text(json.dumps(setup.standard))
    monkeypatch.setenv('NETOPS_NTP_KEY_7', material)
    assert setup.run('--apply')[0] in (cli.EXIT_USAGE, cli.EXIT_FAILED)
    assert not setup.box.calls
