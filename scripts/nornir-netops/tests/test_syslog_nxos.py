from types import SimpleNamespace

import pytest

from netops.core import MODE_REPLACE, render
from netops.features.syslog import FEATURE, audit_fields, parse_logging
from netops import syslog_nxos
from test_syslog import parse_args


def desired(vrf='management', platform='cisco_nxos', severity='informational'):
    document = {'syslog': {'destinations': ['192.0.2.50'], 'severity': severity, 'vrf': 'WRONG'}}
    target = FEATURE.build_desired(parse_args([], document))
    host = SimpleNamespace(platform=platform, data={'syslog_vrf': vrf, 'source_interface': {'syslog': 'Loopback0'}})
    return FEATURE.per_device(target.keys, target.variables, host)


@pytest.mark.parametrize('vrf', ['management', 'default'])
def test_discovered_vrf_rekeys_hosts_and_source(vrf):
    keys, variables = desired(vrf, 'cisco_ios')
    lines = render('syslog', 'cisco_ios', keys, [], variables)
    assert 'WRONG' not in str(lines)
    assert variables['vrf'] == (None if vrf == 'default' else vrf)
    assert not audit_fields(parse_logging('\n'.join(lines)), keys, {'variables': variables})['syslog_audit']['missing']


def test_nxos_reads_effective_vrf_instead_of_assuming_omitted_default():
    config = 'logging server 192.0.2.50\n{192.0.2.50}\nserver VRF: management\nserver severity: notifications'
    entry = syslog_nxos.parse(config)[0]
    assert entry.data['vrf'] == 'management' and entry.data['severity'] == 5
    with pytest.raises(ValueError, match='VRF is missing'):
        syslog_nxos.parse('logging server 192.0.2.50')


def test_nxos_update_does_not_negate_reconfigured_server_and_verifies():
    current = syslog_nxos.parse('logging server 192.0.2.50 5 facility local4 use-vrf default\n'
                               'logging server 192.0.2.99 5 use-vrf default')
    keys, variables = desired()
    context = {'platform': 'cisco_nxos', 'variables': variables}
    add, remove = FEATURE.plan(current, keys, MODE_REPLACE, context)
    commands = render('syslog', 'cisco_nxos', add, remove, variables)
    assert commands == ['logging server 192.0.2.50 6 facility local4 use-vrf management',
                        'logging source-interface Loopback0', 'no logging server 192.0.2.99']
    assert not audit_fields(current, keys, context)['syslog_compliant']
    after = syslog_nxos.parse('\n'.join(commands[:2]))
    assert FEATURE.plan(after, keys, MODE_REPLACE, context) == ([], [])
    assert audit_fields(after, keys, context)['syslog_compliant']


@pytest.mark.parametrize('options', ['secure use-vrf default', 'port 0 use-vrf default',
                                    '8 use-vrf default', '6 use-vrf default secure',
                                    'facility local7;reload use-vrf default'])
def test_nxos_unsupported_or_invalid_settings_fail_closed(options):
    with pytest.raises(ValueError):
        syslog_nxos.parse('logging server 192.0.2.50 ' + options)


def test_nxos_port_change_preserves_unmanaged_severity():
    current = syslog_nxos.parse('logging server 192.0.2.50 4 use-vrf management')
    target = FEATURE.build_desired(parse_args([], {'syslog': {
        'destinations': ['192.0.2.50:1514'], 'vrf': 'management'}}))
    context = {'platform': 'cisco_nxos', 'variables': target.variables}
    add, remove = FEATURE.plan(current, target.keys, MODE_REPLACE, context)
    assert render('syslog', 'cisco_nxos', add, remove, target.variables) == [
        'logging server 192.0.2.50 4 port 1514 facility local7 use-vrf management']


def test_rollback_restores_effective_defaults_explicitly():
    current = syslog_nxos.parse('logging server 192.0.2.50\n{192.0.2.50}\nserver VRF: management')
    reverse = FEATURE.reverse(['logging server 192.0.2.50 6 use-vrf default'], current, [],
                              {'platform': 'cisco_nxos'})
    assert reverse.commands == ['no logging server 192.0.2.50',
                                'logging server 192.0.2.50 5 port 514 facility local7 use-vrf management']
