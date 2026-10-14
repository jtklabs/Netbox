import copy
import json
from types import SimpleNamespace

import pytest

from netops import cli, f5_ntp, ntp_nxos
from netops.core import MODE_REPLACE, canonical_platform, render
from netops.features.ntp import FEATURE, plan_ntp
from netops.ntp_discovery import interface_vrf, source_config
from test_ntp import parse_args
from test_waf import setup as waf_setup


@pytest.mark.parametrize('name,platform', [('nxos', 'cisco_nxos'), ('ios-xe', 'cisco_ios')])
def test_platform_aliases(name, platform):
    assert canonical_platform(name) == platform


def test_nxos_parser_and_idempotent_vrf_source_plan():
    wanted = FEATURE.build_desired(parse_args(['-s', '192.0.2.1,192.0.2.2', '--source', 'mgmt0',
                                              '--vrf', 'management', '--prefer', '192.0.2.1']))
    current = ntp_nxos.parse(ntp_nxos.SAMPLE)
    assert current[0].line == 'ntp server 192.0.2.1 prefer use-vrf management'
    assert current[0].data['vrf'] == 'management'
    assert plan_ntp(current, wanted.keys, MODE_REPLACE,
                    {'platform': 'cisco_nxos', 'variables': dict(wanted.variables)}) == ([], [])


def test_nxos_global_source_change_does_not_reset_servers():
    wanted = FEATURE.build_desired(parse_args(['-s', '192.0.2.1', '--source', 'Loopback0', '--vrf', 'management']))
    variables = copy.deepcopy(wanted.variables)
    current = ntp_nxos.parse('ntp source-interface mgmt0\nntp server 192.0.2.1 use-vrf management')
    add, remove = plan_ntp(current, wanted.keys, MODE_REPLACE, {'platform': 'cisco_nxos', 'variables': variables})
    assert render('ntp', 'cisco_nxos', add, remove, variables) == ['ntp source-interface Loopback0']


def test_nxos_new_server_uses_native_syntax_and_preserves_unmanaged_options():
    wanted = FEATURE.build_desired(parse_args(['-s', '192.0.2.1', '--prefer', '192.0.2.1', '--vrf', 'management']))
    variables = copy.deepcopy(wanted.variables)
    current = ntp_nxos.parse('ntp server 192.0.2.1 minpoll 6 use-vrf management')
    add, remove = plan_ntp(current, wanted.keys, MODE_REPLACE, {'platform': 'cisco_nxos', 'variables': variables})
    commands = render('ntp', 'cisco_nxos', add, remove, variables)
    assert commands == ['no ntp server 192.0.2.1 minpoll 6 use-vrf management',
                        'ntp server 192.0.2.1 prefer use-vrf management minpoll 6']


def test_nxos_default_vrf_and_source_discovery():
    assert ntp_nxos.parse('ntp server 192.0.2.1 use-vrf default')[0].data['vrf'] is None
    found = source_config(ntp_nxos.SAMPLE, 'cisco_nxos')
    assert (found['status'], found['source'], found['vrf']) == ('resolved', 'mgmt0', 'management')
    assert interface_vrf('interface mgmt0\n vrf member management', 'mgmt0') == 'management'


@pytest.mark.parametrize('output', ['ntp server', 'ntp server 192.0.2.1 use-vrf'])
def test_nxos_truncated_server_is_not_treated_as_empty_configuration(output):
    with pytest.raises(ValueError, match='Incomplete'):
        ntp_nxos.parse(output)


@pytest.fixture
def setup(waf_setup):
    waf_setup.standards.write_text(json.dumps({'ntp': {'servers': ['192.0.2.1', '192.0.2.2']}}))
    waf_setup.box.responses[f5_ntp.ENDPOINT] = {'servers': ['192.0.2.9'], 'timezone': 'UTC', 'restrict': []}
    original = waf_setup.run
    waf_setup.run = lambda *args, **kwargs: original(*args, feature='ntp', **kwargs)
    return waf_setup


def test_f5_preview_does_not_write(setup):
    code, report = setup.run('--fail-on-diff')
    assert code == cli.EXIT_DIFF
    assert setup.box.writes == []
    assert report['devices']['f5']['config_before'] == {'servers': ['192.0.2.9']}


@pytest.mark.parametrize('replace', [False, True])
def test_f5_apply_verify_preserve_other_settings_and_idempotence(setup, replace):
    flags = ['--apply'] + (['--replace'] if replace else [])
    code, report = setup.run(*flags)
    assert code == cli.EXIT_OK
    row = report['devices']['f5']
    assert row['verified'] and row['saved']
    assert setup.box.responses[f5_ntp.ENDPOINT]['timezone'] == 'UTC'
    assert setup.box.responses[f5_ntp.ENDPOINT]['restrict'] == []
    assert setup.box.responses[f5_ntp.ENDPOINT]['servers'] == (['192.0.2.1', '192.0.2.2'] if replace else
                                                            ['192.0.2.9', '192.0.2.1', '192.0.2.2'])
    assert setup.run(*flags)[0] == cli.EXIT_OK
    assert len(setup.box.writes) == 1
    assert row['backout']['steps'][0]['body'] == {'servers': ['192.0.2.9']}


@pytest.mark.parametrize('failure', ['invalid', 'include', 'ignore', 'save'])
def test_f5_failures_are_not_compliant(setup, failure):
    if failure == 'invalid':
        setup.box.responses[f5_ntp.ENDPOINT] = {}
    elif failure == 'include':
        setup.box.responses[f5_ntp.ENDPOINT]['include'] = 'server 192.0.2.99'
    elif failure == 'ignore':
        setup.box.ignore.add(f5_ntp.ENDPOINT)
    else:
        setup.box.save_failure = True
    assert setup.run('--apply')[0] == cli.EXIT_FAILED
    assert setup.box.saves == 0


@pytest.mark.parametrize('flags', [('--source', 'Loopback0'), ('--vrf', 'MGMT'), ('--prefer', '192.0.2.1')])
def test_f5_ignores_unsupported_settings(setup, flags):
    assert setup.run('--apply', '--replace', *flags)[0] == cli.EXIT_OK
    assert setup.box.writes == [(f5_ntp.ENDPOINT, {'servers': ['192.0.2.1', '192.0.2.2']})]


@pytest.mark.parametrize('settings', [
    {'authentication': {'key_id': '1'}},
    {'prefer': '192.0.2.1'},
    {'vrf': 'management'},
    {'servers': [{'host': '192.0.2.1', 'prefer': True}]},
    {'regions': {'east': {'servers': ['192.0.2.1'], 'prefer': '192.0.2.1'}}},
    {'authentication': {'key_id': '1'}, 'prefer': '192.0.2.1', 'vrf': 'management'},
])
@pytest.mark.parametrize('apply', [False, True])
def test_f5_shared_settings_do_not_block_server_list(setup, monkeypatch, settings, apply):
    def unexpected_key(*args, **kwargs):
        raise AssertionError('F5 must not resolve NTP keys')

    monkeypatch.setattr('netops.features.ntp.resolve_key', unexpected_key)
    setup.csv.write_text('host,name,platform,region\n192.0.2.1,f5,f5_tmsh,east\n')
    setup.standards.write_text(json.dumps({'ntp': {'servers': ['192.0.2.1'], **settings}}))
    code, report = setup.run('--replace', *(['--apply'] if apply else ['--fail-on-diff']))
    assert code == (cli.EXIT_OK if apply else cli.EXIT_DIFF)
    assert report['devices']['f5']['desired'] == ['192.0.2.1']
    assert setup.box.writes == ([(f5_ntp.ENDPOINT, {'servers': ['192.0.2.1']})] if apply else [])


@pytest.mark.parametrize('data,expected', [
    ({'regions': ['city', 'east', 'country']}, ['192.0.2.20']),
    ({'regions': ['west']}, ['192.0.2.1', '192.0.2.2']),
])
def test_f5_ignores_source_tag_errors_and_uses_regional_servers(setup, data, expected):
    host = SimpleNamespace(name='f5', hostname='192.0.2.1', username='admin', password='test-password',
                           data={**data, 'source_interface': {'ntp': 'Loopback0'},
                                 'source_interface_error': {'ntp': 'Multiple interfaces tagged ntp-source'},
                                 'ntp_vrf': 'management'})
    variables = {'servers': ['192.0.2.1', '192.0.2.2'],
                 'regions': {'east': {'servers': ['192.0.2.20'], 'prefer': '192.0.2.20', 'region': 'east'}},
                 'source': 'Loopback0', 'vrf': 'management', 'manages_auth': True, 'f5': {}}
    original = copy.deepcopy(variables)
    result = f5_ntp.run(SimpleNamespace(host=host), [], variables, MODE_REPLACE, False, True, True)
    assert not result.failed, result.exception
    assert result.result['verified'] and result.result['saved']
    assert setup.box.writes == [(f5_ntp.ENDPOINT, {'servers': expected})]
    assert variables == original


def test_f5_missing_global_and_matching_region_still_fails(setup):
    setup.csv.write_text('host,name,platform,region\n192.0.2.1,f5,f5_tmsh,west\n')
    setup.standards.write_text(json.dumps({'ntp': {'regions': {'east': ['192.0.2.20']}}}))
    code, report = setup.run('--apply')
    assert code == cli.EXIT_FAILED
    assert 'no NTP servers for this device' in report['devices']['f5']['error']
    assert setup.box.writes == []


def test_f5_netbox_inventory_ignores_shared_authentication_and_preference(setup, monkeypatch):
    def unexpected_key(*args, **kwargs):
        raise AssertionError('F5 must not resolve NTP keys')

    monkeypatch.setattr('netops.features.ntp.resolve_key', unexpected_key)
    setup.standards.write_text(json.dumps({'ntp': {'servers': ['192.0.2.1'],
        'authentication': {'key_id': '1'}, 'prefer': '192.0.2.1', 'vrf': 'management'}}))
    code, report = setup.run('--apply', '--replace', netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert report['devices']['f5']['verified']
    assert setup.box.writes == [(f5_ntp.ENDPOINT, {'servers': ['192.0.2.1']})]


def test_mixed_inventory_still_requires_authentication_for_other_platforms(monkeypatch):
    args = parse_args((), {'ntp': {'servers': ['192.0.2.1'], 'authentication': {'key_id': '1'}}})
    args.ntp_platforms = ['f5_tmsh', 'cisco_ios']
    monkeypatch.setenv('NETOPS_NTP_KEY_1', 'shared-test-key')
    desired = FEATURE.build_desired(args)
    assert desired.variables['manages_auth']
    assert desired.secrets == ['shared-test-key']


@pytest.mark.parametrize('status,expected', [(401, 'authentication failed'), (403, 'access denied')])
def test_f5_connection_error_reaches_queued_feature_findings(setup, status, expected):
    from netops.errors import DeviceHTTPError
    from netops.upgrade.remediation import report_details
    setup.box.responses[f5_ntp.ENDPOINT] = DeviceHTTPError('F5', 'GET', f5_ntp.ENDPOINT, status)
    code, report = setup.run('--apply')
    assert code == cli.EXIT_FAILED
    assert expected in report_details(report)['error']
    assert setup.box.writes == []
