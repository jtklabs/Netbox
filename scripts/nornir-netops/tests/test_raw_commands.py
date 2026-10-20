from pathlib import Path
from types import SimpleNamespace
import json

import pytest
import yaml
from nornir.core.inventory import Host
from nornir.core.task import Result

from netops import cli, runner
from netops.features import raw_commands as raw
from netops.raw_commands_policy import validate_raw_commands
from netops.standards import Standards
from netops.upgrade import remediation


def bundle(value='default'):
    return {'commands': ['description ' + value], 'expect': ['description ' + value]}


def settings(platform='cisco_ios'):
    return {'enabled': True, 'platforms': {platform: bundle()}}


def test_region_ancestry_default_and_regional_only():
    policy = settings()
    local = policy['platforms']['cisco_ios']
    local['regions'] = {'us': bundle('parent'), 'us-east': bundle('child')}
    host = Host('switch', platform='ios', data={'regions': ['us-east', 'us']})
    assert raw.select(policy, host) == (bundle('child'), 'us-east')
    host.data['regions'] = ['unconfigured-child', 'us']
    assert raw.select(policy, host) == (bundle('parent'), 'us')
    host.data = {'region': 'US-EAST'}
    assert raw.select(policy, host) == (bundle('child'), 'us-east')
    host.data = {'region': 'elsewhere'}
    assert raw.select(policy, host) == (bundle(), None)
    del local['commands']
    del local['expect']
    with pytest.raises(ValueError, match='no platform default'):
        raw.select(policy, host)
    with pytest.raises(ValueError, match='platform'):
        raw.select(policy, Host('unknown', platform='f5_tmsh'))
    policy['enabled'] = False
    with pytest.raises(ValueError, match='disabled'):
        raw.select(policy, host)


@pytest.mark.parametrize('mutation', [
    lambda p: p.pop('enabled'), lambda p: p.update(enabled='true'),
    lambda p: p.update(platforms={}), lambda p: p['platforms'].update(ios=bundle()),
    lambda p: p['platforms']['cisco_ios'].update(commands=None),
    lambda p: p['platforms']['cisco_ios'].update(commands=['one\ntwo']),
    lambda p: p['platforms']['cisco_ios'].update(expect=[], reject=[]),
    lambda p: p['platforms']['cisco_ios'].update(expect='description default'),
    lambda p: p['platforms']['cisco_ios'].update(regions={'US': bundle()}),
    lambda p: p['platforms']['cisco_ios'].update(regions={'us': {'commands': ['one']}}),
    lambda p: p['platforms']['cisco_ios'].update(commands=['write memory']),
])
def test_invalid_yaml_rejected(mutation):
    policy = settings()
    mutation(policy)
    with pytest.raises(ValueError):
        raw.build_desired(SimpleNamespace(standards=Standards(document={'raw_commands': policy})))


def test_checks_are_literal_exact_lines_and_rejected_fragments():
    policy = {'expect': ['mac hmac-sha2-512'], 'reject': ['hmac-sha1']}
    assert raw.missing('   mac hmac-sha2-512\n!', policy) == []
    assert len(raw.missing('mac hmac-sha2-512 hmac-sha1', policy)) == 2
    assert raw.missing('description {{value}}', {'expect': ['description {{value}}']}) == []


@pytest.mark.parametrize('platform', ['cisco_ios', 'arista_eos', 'cisco_asa', 'cisco_nxos'])
@pytest.mark.parametrize('custom_show', [False, True, 'regex'])
def test_cli_dry_run_exact_apply_verify_repeat_and_failed_readback(tmp_path, monkeypatch, platform, custom_show):
    monkeypatch.setenv('NET_USER', 'operator')
    monkeypatch.setenv('NET_PASS', 'test-password')
    csv = tmp_path / 'devices.csv'
    csv.write_text(f'host,name,platform,region\n192.0.2.1,device,{platform},us-east\n')
    path = tmp_path / 'standards.yaml'
    policy = settings(platform)
    regional = {'commands': ['interface Ethernet1', 'description {{literal}}', 'exit'],
                'expect': ['description {{literal}}'], 'reject': ['description old']}
    if custom_show:
        regional['show'] = 'show interfaces description'
        regional['contains'] = ['{{literal}}']
        if custom_show == 'regex':
            regional['contains'] = [{'regex': r'^description \{\{literal\}\}$'}]
            regional['reject'] = [{'regex': '(?i)description old'}]
        del regional['expect']
        policy['platforms'][platform]['show'] = 'show version'
    policy['platforms'][platform]['regions'] = {'us-east': regional}
    path.write_text(yaml.safe_dump({'raw_commands': policy}))
    report = tmp_path / 'report.json'
    state = {'output': 'description old', 'writes': [], 'saves': []}

    def read(task, command_string, **kwargs):
        if command_string.startswith('show '):
            assert command_string == regional.get('show', raw.SHOW[platform])
            return Result(host=task.host, result=state['output'])
        state['saves'].append(command_string)
        return Result(host=task.host, result='OK')

    def push(task, config_commands, **kwargs):
        state['writes'].append(config_commands)
        state['output'] = 'description {{literal}}'
        return Result(host=task.host, result='OK')

    monkeypatch.setattr(runner, 'netmiko_send_command', read)
    monkeypatch.setattr(runner, 'netmiko_send_config', push)

    def invoke(*flags):
        code = cli.main(['raw-commands', '--no-env-file', '--csv', str(csv),
                         '--standards', str(path), '--report', str(report), *flags])
        return code, json.loads(report.read_text())['devices']['device']

    assert invoke('--add', '--apply', '--yes', '--fail-on-diff')[0] == cli.EXIT_DIFF
    assert not state['writes']
    code, row = invoke('--replace', '--fail-on-diff')
    assert code == cli.EXIT_DIFF and row['commands'] == regional['commands']
    assert row['selected_region'] == 'us-east' and not row['rollback']
    assert row['config_before']['show_command'] == regional.get('show', raw.SHOW[platform])
    assert row['read_steps'][0]['command'] == regional.get('show', raw.SHOW[platform])
    assert not state['writes']
    if custom_show == 'regex':
        pattern = regional['contains'][0]['regex']
        assert row['desired'] == ['contains regex: ' + pattern, 'reject regex: (?i)description old']
        assert row['config_before']['failed_checks'] == [
            'missing regex: ' + pattern, 'forbidden regex: (?i)description old']
    code, row = invoke('--replace', '--apply', '--yes')
    assert code == cli.EXIT_OK and row['verified'] and row['saved']
    assert state['writes'] == [regional['commands']]
    assert invoke('--replace', '--apply', '--yes')[1]['compliant']
    assert len(state['writes']) == len(state['saves']) == 1
    state['output'] = 'description old'
    monkeypatch.setattr(runner, 'netmiko_send_config', lambda task, **kw: Result(host=task.host, result='OK'))
    code, row = invoke('--replace', '--apply', '--yes')
    assert code == cli.EXIT_FAILED and row['verified'] is False and row['saved'] is False
    assert row['missing_after'] and len(state['saves']) == 1
    if custom_show == 'regex':
        assert row['missing_after'][0] == 'missing regex: ' + pattern
    state['output'] = '% Invalid input detected'
    assert invoke('--replace', '--apply', '--yes')[0] == cli.EXIT_FAILED
    state['output'] = ''
    assert invoke('--replace', '--apply', '--yes')[0] == cli.EXIT_FAILED


class Junos:
    def __init__(self, failure=None):
        self.output = 'set system host-name old'
        self.calls = []
        self.failure = failure

    def send_command(self, command, **kwargs):
        self.calls.append(command)
        if command.removeprefix('run ') in (raw.SHOW['juniper_junos'], 'show system information'):
            return self.output
        if command == 'show | compare':
            return 'pending changes' if self.failure == 'dirty' else ''
        if command == 'run show system commit':
            return 'rollback in 4mins' if self.failure == 'pending' else ''
        if command == 'rollback 0':
            return ''
        if command == 'commit check':
            return 'error: check failed' if self.failure == 'check' else 'configuration check succeeds'
        if command == 'commit confirmed 5':
            if self.failure == 'commit':
                raise EOFError()
            if self.failure != 'verify':
                self.output = 'set system host-name new'
            return 'commit complete'
        if command == 'commit':
            return 'commit complete'
        raise AssertionError(command)

    def config_mode(self, config_command):
        self.calls.append(config_command)
        return ''

    def send_config_set(self, commands, **kwargs):
        self.calls.extend(commands)
        assert commands == ['set system host-name new']
        assert kwargs == {'exit_config_mode': False}
        return 'error: invalid' if self.failure == 'write' else ''

    def find_prompt(self):
        return 'operator@srx#'

    def exit_config_mode(self):
        self.calls.append('exit')


def run_junos(box, monkeypatch, show=None, **options):
    monkeypatch.setattr(Host, 'get_connection', lambda *args: box)
    task = SimpleNamespace(host=Host('srx', platform='junos'), nornir=SimpleNamespace(config=None))
    policy = {'enabled': True, 'platforms': {'juniper_junos': {
        'commands': ['set system host-name new'], 'expect': ['set system host-name new']}}}
    if show:
        policy['platforms']['juniper_junos']['show'] = show
    return raw.run(task, [], {'raw_commands': policy}, **dict(
        dict(mode='replace', dry_run=False, save=True, verify=True), **options))


def test_junos_confirmed_commit_and_repeat(monkeypatch):
    box = Junos()
    assert run_junos(box, monkeypatch, dry_run=True).result['commands']
    assert box.calls == [raw.SHOW['juniper_junos']]
    result = run_junos(box, monkeypatch)
    assert not result.failed and result.result['saved'] and result.result['verified']
    assert box.calls.index('commit confirmed 5') < box.calls.index('commit')
    box.calls.clear()
    assert run_junos(box, monkeypatch).result['compliant']
    assert box.calls == [raw.SHOW['juniper_junos']]


@pytest.mark.parametrize('failure', ['dirty', 'pending', 'write', 'check', 'commit', 'verify'])
def test_junos_failures_never_confirm(monkeypatch, failure):
    box = Junos(failure)
    result = run_junos(box, monkeypatch)
    assert result.failed and 'commit' not in box.calls
    if failure in ('write', 'check'):
        assert 'rollback 0' in box.calls
    if failure in ('commit', 'verify'):
        assert result.result['advisories'] and 'rollback 0' not in box.calls


@pytest.mark.parametrize('options', [{'save': False}, {'verify': False}])
def test_junos_requires_verified_persistence(monkeypatch, options):
    box = Junos()
    assert run_junos(box, monkeypatch, **options).failed
    assert box.calls == [raw.SHOW['juniper_junos']]


def test_worker_translation_and_validation_contract():
    assert remediation.validate({'features': ['raw_commands'], 'mode': 'replace'}) == (['raw_commands'], 'replace')
    assert remediation.command('raw_commands', 1, 'replace', False, SimpleNamespace())[2] == 'raw-commands'
    root = Path(__file__).resolve().parents[3]
    assert (root / 'scripts/nornir-netops/netops/raw_commands_policy.py').read_text() == (
        root / 'plugins/netbox-compliance/netbox_compliance/raw_commands_policy.py').read_text()


@pytest.mark.parametrize('regional', [False, True])
def test_netbox_requests_region_ancestry_for_regional_bundles(monkeypatch, regional):
    import nornir
    from netops import netbox
    policy = settings()
    if regional:
        policy['platforms']['cisco_ios']['regions'] = {'us': bundle('regional')}
    monkeypatch.setattr(netbox, 'settings_from', lambda *args: {
        'url': 'https://netbox.example', 'token': 'test', 'verify_tls': True, 'source_tags': {'ntp': 'ntp-source'}})
    monkeypatch.setattr(nornir, 'InitNornir', lambda **kwargs: kwargs)
    args = SimpleNamespace(feature=raw.FEATURE, key_file=None, conn_timeout=10, port=22)
    creds = SimpleNamespace(username='operator', password='test', secret=None)
    result = netbox.init_nornir(args, creds, Standards(document={'raw_commands': policy}), 1)
    options = result['inventory']['options']
    assert options['regions'] is regional
    assert not options['source_tags']


@pytest.mark.parametrize('commands', [['commit'], ['set system host-name x; commit'], ['run request system reboot']])
def test_junos_rejects_transaction_or_exec_commands(commands):
    with pytest.raises(ValueError):
        validate_raw_commands({'enabled': True, 'platforms': {'juniper_junos': {
            'commands': commands, 'expect': ['set system host-name x']}}})


def test_disabled_feature_fails_before_connecting(monkeypatch):
    monkeypatch.setattr(Host, 'get_connection', lambda *args: pytest.fail('must not connect'))
    policy = settings('juniper_junos')
    policy['enabled'] = False
    task = SimpleNamespace(host=Host('srx', platform='junos'), nornir=SimpleNamespace(config=None))
    result = raw.run(task, [], {'raw_commands': policy}, 'replace', False, True, True)
    assert result.failed and not result.changed and not result.result['compliant']


def test_optional_selftest_does_not_require_a_raw_standard():
    args = SimpleNamespace(standards=Standards(document={}), _selftest=True)
    assert raw.selftest(raw.build_desired(args)) == 0
    args._selftest = False
    with pytest.raises(ValueError):
        raw.build_desired(args)


@pytest.mark.parametrize('output', ['ERROR: command rejected', '% Invalid input detected',
                                   'syntax error', 'Permission denied'])
def test_platform_cli_errors_are_never_success(output):
    with pytest.raises(ValueError):
        raw.checked_cli('raw commands', output)


@pytest.mark.parametrize('show', ['show ip ssh', 'show running-config | include ^ip ssh',
                                 'show configuration system services ssh | display set | no-more'])
def test_custom_show_and_contains_validate(show):
    policy = settings()
    policy['platforms']['cisco_ios'] = {'commands': ['description new'], 'show': show,
                                      'contains': ['some text'], 'reject': ['bad text']}
    validate_raw_commands(policy)
    assert raw.missing('prefix some text suffix', policy['platforms']['cisco_ios']) == []
    assert raw.missing('bad text', policy['platforms']['cisco_ios']) == [
        'missing text: some text', 'forbidden text: bad text']


@pytest.mark.parametrize('show', [None, False, [], '', 'show', 'configure terminal',
    'show version\nreload', 'show version; reload', 'show version | save disk0:file',
    'show version > disk0:file', 'show version | redirect tftp://example/file',
    'show version |', 'show version && reload', 'show version | tee file'])
def test_custom_show_rejects_non_show_and_output_writes(show):
    policy = settings()
    policy['platforms']['cisco_ios']['show'] = show
    with pytest.raises(ValueError):
        validate_raw_commands(policy)


@pytest.mark.parametrize('contains', ['', False, [None], ['line\nbreak'], []])
def test_contains_requires_valid_nonempty_checks(contains):
    policy = settings()
    policy['platforms']['cisco_ios'] = {'commands': ['description new'], 'contains': contains}
    with pytest.raises(ValueError):
        validate_raw_commands(policy)


def test_junos_custom_show_keeps_configuration_race_check(monkeypatch):
    box = Junos()
    result = run_junos(box, monkeypatch, show='show system information')
    assert not result.failed and result.result['verified']
    assert box.calls[:2] == ['show system information', raw.SHOW['juniper_junos']]
    assert 'run ' + raw.SHOW['juniper_junos'] in box.calls
    assert 'run show system information' in box.calls
    assert box.calls.index('run show system information') < box.calls.index('commit')


def test_junos_operational_output_changes_do_not_trip_config_guard(monkeypatch):
    class OperationalJunos(Junos):
        def send_command(self, command, **kwargs):
            output = super().send_command(command, **kwargs)
            if command.removeprefix('run ') == 'show system information':
                return output + '\nuptime: ' + str(len(self.calls))
            return output

    box = OperationalJunos()
    result = run_junos(box, monkeypatch, show='show system information')
    assert not result.failed and result.result['verified']


def test_junos_custom_show_does_not_hide_concurrent_config_change(monkeypatch):
    class ConcurrentJunos(Junos):
        def config_mode(self, config_command):
            result = super().config_mode(config_command)
            self.output = 'set system host-name changed-elsewhere'
            return result

    box = ConcurrentJunos()
    result = run_junos(box, monkeypatch, show='show system information')
    assert result.failed and 'configuration changed' in str(result.exception)
    assert 'set system host-name new' not in box.calls and 'commit' not in box.calls


@pytest.mark.parametrize('regional', [False, True])
def test_contains_mixes_literal_and_regex_checks(regional):
    policy = settings()
    checks = {'commands': ['description new'], 'contains': [
        '[literal].*', {'regex': r'^MACs: hmac-sha2-(?:256|512)$'},
        {'regex': r'(?i)^hostname: switch\d+$'}]}
    if regional:
        policy['platforms']['cisco_ios']['regions'] = {'us': checks}
    else:
        policy['platforms']['cisco_ios'] = checks
    validate_raw_commands(policy)
    output = 'header\n[literal].*\nMACs: hmac-sha2-512\nHOSTNAME: switch12\nfooter'
    assert raw.missing(output, checks) == []
    assert raw.missing(output.replace('512', '512-weak'), checks) == [
        r'missing regex: ^MACs: hmac-sha2-(?:256|512)$']
    assert raw.missing(output.replace('[literal].*', 'literal'), checks) == [
        'missing text: [literal].*']
    assert raw.missing(output.replace('MACs:', 'macs:'), checks) == [
        r'missing regex: ^MACs: hmac-sha2-(?:256|512)$']


def test_contains_regex_search_and_multiline_semantics():
    assert raw.missing('prefix hmac-sha2-512 suffix', {'contains': [{'regex': r'\bhmac-sha2-512\b'}]}) == []
    assert raw.missing('header\nline\nfooter', {'contains': [{'regex': '^line$'}]}) == []
    assert raw.missing('first\nsecond', {'contains': [{'regex': '^first.*second$'}]})
    assert raw.missing('first\nsecond', {'contains': [{'regex': '(?s)^first.*second$'}]}) == []


@pytest.mark.parametrize('regional', [False, True])
@pytest.mark.parametrize('item', [
    {}, {'regex': None}, {'regex': False}, {'regex': []}, {'regex': 123},
    {'regex': ''}, {'regex': '  '}, {'regex': 'a\nb'}, {'regex': 'a' * 2049},
    {'regex': '['}, {'regex': r'\q'}, {'regex': r'a{999999999999999999999}'},
    {'regex': 'ok', 'flags': 'i'}, {'regexp': 'ok'},
])
def test_contains_rejects_invalid_regex_items_before_execution(item, regional):
    policy = settings()
    checks = {'commands': ['description new'], 'contains': [item]}
    if regional:
        policy['platforms']['cisco_ios']['regions'] = {'us': checks}
    else:
        policy['platforms']['cisco_ios'] = checks
    with pytest.raises(ValueError, match=r'contains\[0\]'):
        raw.build_desired(SimpleNamespace(standards=Standards(document={'raw_commands': policy})))


def test_contains_limits_mixed_check_count():
    policy = settings()
    policy['platforms']['cisco_ios']['contains'] = [{'regex': '^ok$'}] * 257
    with pytest.raises(ValueError, match='256'):
        validate_raw_commands(policy)


@pytest.mark.parametrize('alias', ['infoblox_nios', 'infoblox-nios', 'infoblox', 'nios'])
def test_infoblox_cli_audit_preview_apply_verify_and_repeat(tmp_path, monkeypatch, alias):
    monkeypatch.setenv('NET_USER', 'operator')
    monkeypatch.setenv('NET_PASS', 'test-password')
    csv = tmp_path / 'devices.csv'
    csv.write_text(f'host,name,platform,region\n192.0.2.1,dns1,{alias},us-east\n')
    path = tmp_path / 'standards.yaml'
    checks = {'commands': ['set tcp_timestamps enable', 'set tcp_timestamps disable'],
              'show': 'show tcp_timestamps',
              'contains': [{'regex': r'^TCP timestamps are disabled\.$'}]}
    policy = {'enabled': True, 'platforms': {'infoblox_nios': {
        'regions': {'us-east': checks}}}}
    path.write_text(yaml.safe_dump({'raw_commands': policy}))
    report = tmp_path / 'report.json'
    state = {'output': 'TCP timestamps are enabled.', 'calls': [], 'closed': []}

    class Connection:
        def send_command(self, command, read_timeout):
            assert read_timeout == 60
            state['calls'].append(command)
            if command == 'show tcp_timestamps':
                return state['output']
            assert command in checks['commands']
            state['output'] = 'TCP timestamps are disabled.'
            return ''

    def connect(host, kind, config):
        assert kind == 'netmiko' and host.platform == 'generic'
        assert host.username == 'operator' and host.password == 'test-password'
        return Connection()

    def close(host, kind):
        assert host.platform == 'infoblox_nios'
        state['closed'].append(kind)

    monkeypatch.setattr(Host, 'get_connection', connect)
    monkeypatch.setattr(Host, 'close_connection', close)
    monkeypatch.setattr(runner, 'netmiko_send_config', lambda *a, **kw: pytest.fail('NIOS has no configuration mode'))

    def invoke(*flags):
        code = cli.main(['raw-commands', '--no-env-file', '--csv', str(csv),
                         '--standards', str(path), '--report', str(report), *flags])
        return code, json.loads(report.read_text())['devices']['dns1']

    assert invoke('--add', '--apply', '--yes', '--fail-on-diff')[0] == cli.EXIT_DIFF
    assert state['calls'] == ['show tcp_timestamps']
    state['calls'].clear()
    code, row = invoke('--replace', '--fail-on-diff')
    assert code == cli.EXIT_DIFF and not row['applied']
    assert row['commands'] == checks['commands'] and row['save_command'] is None
    assert state['calls'] == ['show tcp_timestamps']
    state['calls'].clear()
    code, row = invoke('--replace', '--apply', '--yes')
    assert code == cli.EXIT_OK and row['verified'] and row['applied']
    assert row['platform'] == 'infoblox_nios' and row['selected_region'] == 'us-east'
    assert row['saved'] is None and row['save_command'] is None
    assert state['calls'] == ['show tcp_timestamps', *checks['commands'], 'show tcp_timestamps']
    assert row['config_after']['failed_checks'] == []
    state['calls'].clear()
    assert invoke('--replace', '--apply', '--yes')[1]['compliant']
    assert state['calls'] == ['show tcp_timestamps']
    assert state['closed'] == ['netmiko'] * 4


@pytest.mark.parametrize('failure', ['read', 'empty', 'command', 'timeout', 'verify', 'connect'])
def test_infoblox_failures_stop_and_restore_platform(monkeypatch, failure):
    calls, closed = [], []
    checks = {'commands': ['set tcp_timestamps enable', 'set tcp_timestamps disable'],
              'show': 'show tcp_timestamps', 'expect': ['TCP timestamps are disabled.']}

    class Connection:
        def send_command(self, command, **kwargs):
            calls.append(command)
            if command == 'show tcp_timestamps':
                return {'read': 'Unknown command: tcp_timestamps', 'empty': ''}.get(
                    failure, 'TCP timestamps are enabled.')
            if command == checks['commands'][0]:
                if failure == 'timeout':
                    raise TimeoutError('Prompt not returned')
                return ''
            if failure == 'command':
                return 'Error: command rejected'
            return ''

    def connect(host, kind, config):
        assert host.platform == 'generic'
        if failure == 'connect':
            raise ConnectionError('Login failed')
        return Connection()

    monkeypatch.setattr(Host, 'get_connection', connect)
    monkeypatch.setattr(Host, 'close_connection', lambda host, kind: closed.append(kind))
    host = Host('dns', platform='infoblox-nios')
    task = SimpleNamespace(host=host, nornir=SimpleNamespace(config=None))
    result = raw.run(task, [], {'raw_commands': {'enabled': True, 'platforms': {
        'infoblox_nios': checks}}}, 'replace', False, True, True)
    assert result.failed and host.platform == 'infoblox-nios'
    assert result.result['saved'] is None
    assert closed == ([] if failure == 'connect' else ['netmiko'])
    if failure in ('read', 'empty', 'connect'):
        assert not result.changed and not result.result['applied']
        assert calls == ([] if failure == 'connect' else [checks['show']])
    elif failure == 'timeout':
        assert result.changed and calls == [checks['show'], checks['commands'][0]]
    elif failure == 'command':
        assert result.changed and result.result['applied']
        assert calls == [checks['show'], *checks['commands']]
    else:
        assert result.changed and result.result['verified'] is False
        assert result.result['missing_after'] == ['missing line: TCP timestamps are disabled.']
        assert 'may remain active' in str(result.exception)


@pytest.mark.parametrize('regional', [False, True])
@pytest.mark.parametrize('mutation', [
    lambda p: p.pop('show'), lambda p: p.update(commands=['configure terminal']),
    lambda p: p.update(commands=['set']), lambda p: p.update(commands=['y']),
    lambda p: p.update(commands=['set tcp_timestamps disable; reboot']),
    lambda p: p.update(commands=['set tcp_timestamps disable | show status']),
    lambda p: p.update(commands=['set tcp_timestamps disable && reboot']),
])
def test_infoblox_validates_explicit_show_and_direct_commands(regional, mutation):
    checks = {'commands': ['set tcp_timestamps disable'], 'show': 'show tcp_timestamps',
              'contains': ['disabled']}
    mutation(checks)
    policy = {'enabled': True, 'platforms': {'infoblox_nios': (
        {'regions': {'us': checks}} if regional else checks)}}
    with pytest.raises(ValueError):
        validate_raw_commands(policy)


@pytest.mark.parametrize('output', ['Unknown command: bad', 'Invalid argument', 'Usage: set ...',
                                    'Error setting value', 'Permission denied', 'Not authorized'])
def test_infoblox_rejected_cli_output_cannot_pass_checks(output):
    with pytest.raises(ValueError):
        raw.checked_nios('show tcp_timestamps', output)


@pytest.mark.parametrize('platform', sorted(raw.SHOW) + ['infoblox_nios'])
@pytest.mark.parametrize('regional', [False, True])
@pytest.mark.parametrize('empty_commands', [False, True])
@pytest.mark.parametrize('output,compliant', [
    ('*~192.0.2.1 192.0.2.2 2 10 64 377 0.1 0.1 0.1', True),
    ('*~192.0.2.1 good\n ~192.0.2.2 .INIT. 16', False),
    (' ~192.0.2.1 good', False),
    (' ~192.0.2.1 good\n * sys.peer, # selected, + candidate, ~ configured', False),
])
def test_read_only_checks_never_write_even_with_apply(monkeypatch, platform, regional, empty_commands, output, compliant):
    checks = {'show': 'show ntp associations', 'contains': [{'regex': r'^\s*\*~?\S+\s+'}],
              'reject': [{'regex': r'(?i)\bINIT\b'}]}
    if empty_commands:
        checks['commands'] = []
    policy = {'enabled': True, 'platforms': {platform: (
        {'regions': {'us': checks}} if regional else checks)}}
    validate_raw_commands(policy)
    calls = []
    host = Host('switch', platform=platform, data={'regions': ['us']})

    def read(command, **kwargs):
        calls.append(command)
        assert command == checks['show']
        return output

    monkeypatch.setattr(Host, 'get_connection', lambda *a: SimpleNamespace(send_command=read))
    monkeypatch.setattr(Host, 'close_connection', lambda *a: None)
    task = SimpleNamespace(host=host, nornir=SimpleNamespace(config=None),
                           run=lambda **kw: Result(host=host, result=read(kw['command_string'])))
    # No verification/save is necessary for an assertion-only run.
    result = raw.run(task, [], {'raw_commands': policy}, 'replace', False, False, False)
    assert not result.failed and not result.changed
    row = result.result
    assert row['read_only'] and row['compliant'] is compliant
    assert not row['commands'] and not row['add'] and not row['applied']
    assert row['saved'] is None and row['verified'] is None and row['save_command'] is None
    assert bool(row['config_before']['failed_checks']) is not compliant
    assert calls == [checks['show']]


@pytest.mark.parametrize('mode', ['--add', '--replace'])
@pytest.mark.parametrize('apply', [False, True])
@pytest.mark.parametrize('output,code', [
    ('address ref clock\n ~192.0.2.1 .INIT. 16', cli.EXIT_DIFF),
    ('address ref clock\n ~192.0.2.1 .init. 16', cli.EXIT_DIFF),
    ('address ref clock\n*~192.0.2.1 192.0.2.2 2', cli.EXIT_OK),
    ('', cli.EXIT_FAILED),
    ('% Invalid input detected', cli.EXIT_FAILED),
])
def test_read_only_cli_absence_check_reports_pass_fail(tmp_path, monkeypatch, mode, apply, output, code):
    monkeypatch.setenv('NET_USER', 'operator')
    monkeypatch.setenv('NET_PASS', 'test-password')
    csv = tmp_path / 'devices.csv'
    csv.write_text('host,name,platform\n192.0.2.1,switch,cisco_ios\n')
    path = tmp_path / 'standards.yaml'
    path.write_text(yaml.safe_dump({'raw_commands': {'enabled': True, 'platforms': {'cisco_ios': {
        'show': 'show ntp associations', 'reject': [{'regex': r'(?i)\bINIT\b'}]}}}}))
    report = tmp_path / 'report.json'

    def read(task, command_string, **kwargs):
        assert command_string == 'show ntp associations'
        return Result(host=task.host, result=output)

    monkeypatch.setattr(runner, 'netmiko_send_command', read)
    monkeypatch.setattr(runner, 'netmiko_send_config', lambda *a, **kw: pytest.fail('must never configure'))
    flags = ['--apply', '--yes'] if apply else []
    assert cli.main(['raw-commands', '--no-env-file', '--csv', str(csv), '--standards', str(path),
                     '--report', str(report), mode, *flags]) == code
    row = json.loads(report.read_text())['devices']['switch']
    assert row['compliant'] is (code == cli.EXIT_OK)
    assert not row['commands'] and not row['applied']
    if code == cli.EXIT_DIFF:
        assert row['current'] == [r'forbidden regex: (?i)\bINIT\b']


@pytest.mark.parametrize('check', ['contains', 'reject'])
@pytest.mark.parametrize('value', [None, '', [{'regex': '['}], [{'regex': False}], [{'pattern': 'INIT'}]])
def test_read_only_check_validation(check, value):
    with pytest.raises(ValueError):
        validate_raw_commands({'enabled': True, 'platforms': {'cisco_ios': {
            'show': 'show ntp associations', check: value}}})


def test_reject_literal_and_regex_semantics():
    checks = {'reject': ['[literal].*', {'regex': r'(?i)\bINIT\b'}, {'regex': '^bad$'}]}
    assert raw.missing('initialized\nnot bad\nliteral', checks) == []
    assert raw.missing('header\n.init.\nbad\n[literal].*', checks) == [
        'forbidden text: [literal].*', r'forbidden regex: (?i)\bINIT\b', 'forbidden regex: ^bad$']


def test_ssh_mac_feature_is_retired():
    from netops.features import FEATURES
    assert 'ssh-mac' not in FEATURES
    with pytest.raises(ValueError):
        remediation.validate({'features': ['ssh_mac'], 'mode': 'replace'})
