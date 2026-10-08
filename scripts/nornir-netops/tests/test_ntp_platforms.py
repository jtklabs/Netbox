import copy
import json

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
def test_f5_rejects_unsupported_settings_before_write(setup, flags):
    assert setup.run('--apply', *flags)[0] == cli.EXIT_FAILED
    assert setup.box.writes == []
