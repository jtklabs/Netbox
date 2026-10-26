"""Account discovery never replays work or exposes credential material."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nornir.core.inventory import Host
from netmiko import NetmikoAuthenticationException

from netops.credentials import CredentialError, Credentials, resolve
from netops.credential_profiles import Profile, Selector, prepare, load_profiles, rejected
from netops.inventory import init_from_records, missing_credentials
from netops.errors import DeviceHTTPError


PROFILES = (Profile('primary', 'user-one', 'password-one', 'enable-one'),
            Profile('legacy', 'user-two', 'password-two'),
            Profile('local', 'user-three', 'password-three'))


class Store:
    def __init__(self, preferred=''):
        self.preferred = preferred
        self.calls = []

    def request_object(self, method, path, payload):
        self.calls.append(payload)
        if payload['action'] == 'begin':
            profiles = list(payload['profiles'])
            if self.preferred:
                profiles = [self.preferred]
            return {'token': 'private-lease', 'profiles': profiles[:payload['max_attempts']], 'pinned': bool(self.preferred)}
        self.preferred = payload['profile'] or self.preferred
        return {'status': payload['status']}


def selector(store=None):
    host = Host(name='switch', hostname='192.0.2.1', platform='cisco_ios', data={'netbox_id': 7})
    return Selector(host, PROFILES, store or Store())


def test_discovers_then_other_worker_uses_saved_profile_first():
    store = Store()
    first = selector(store)
    attempts = []

    def connect():
        attempts.append(first.host.username)
        if first.host.username == 'user-one':
            raise NetmikoAuthenticationException('Authentication failed.')
        return 'connection'

    assert first.run(connect) == 'connection'
    assert attempts == ['user-one', 'user-two']
    second = selector(store)
    assert second.run(lambda: second.host.username) == 'user-two'
    assert store.calls[-1]['attempted'] == ['legacy']
    assert not any(value in repr(store.calls) for p in PROFILES for value in (p.username, p.password))


@pytest.mark.parametrize('error', [NetmikoAuthenticationException('Authentication failed.'),
                                  DeviceHTTPError('F5', 'POST', '/login', 401), TimeoutError('Still booting')])
def test_saved_account_is_retained_across_failed_runs(error):
    store = Store('legacy')
    failed = selector(store)
    connection = Mock(side_effect=error)
    with pytest.raises((CredentialError, TimeoutError)):
        failed.run(connection)
    connection.assert_called_once()
    assert store.calls[-1]['attempted'] == ['legacy']
    assert store.preferred == 'legacy'
    recovered = selector(store)
    assert recovered.run(lambda: recovered.host.username) == 'user-two'
    assert store.calls[-1]['attempted'] == ['legacy']


def test_reboot_reconnect_retries_use_same_account_without_rediscovery():
    selected = selector(Store('legacy'))
    assert selected.run(lambda: 'initial connection') == 'initial connection'
    calls = len(selected.client.calls)
    for error in (NetmikoAuthenticationException('Authentication failed.'), TimeoutError('Still booting')):
        with pytest.raises(type(error)):
            selected.run(Mock(side_effect=error))
        assert selected.host.username == 'user-two'
    assert selected.run(lambda: 'reconnected') == 'reconnected'
    assert len(selected.client.calls) == calls


@pytest.mark.parametrize('error', [TimeoutError('unreachable'),
    NetmikoAuthenticationException('Authentication timeout'),
    NetmikoAuthenticationException('transport shut down'),
    ValueError('Failed to enter enable mode'), DeviceHTTPError('F5', 'POST', '/login', 403)])
def test_ambiguous_or_authorization_failures_never_try_another_account(error):
    selected = selector()
    connect = Mock(side_effect=error)
    with pytest.raises(type(error)):
        selected.run(connect)
    assert connect.call_count == 1
    assert selected.client.calls[-1]['status'] == 'unavailable'
    with pytest.raises(CredentialError):
        selected.run(connect)
    assert connect.call_count == 1


def test_f5_401_allows_fallback_but_reconnect_keeps_selected_account():
    selected = selector()
    connect = Mock(side_effect=[DeviceHTTPError('F5', 'POST', '/login', 401), 'token'])
    assert selected.run(connect, 'https') == 'token'
    assert selected.host.data['credential_profile'] == 'legacy'
    assert selected.client.calls[0]['transport'] == 'https'
    calls = len(selected.client.calls)
    with pytest.raises(DeviceHTTPError):
        selected.run(Mock(side_effect=DeviceHTTPError('F5', 'POST', '/login', 401)), 'https')
    assert len(selected.client.calls) == calls
    assert selected.host.username == 'user-two'


def test_attempt_limit_and_no_retries_in_same_run():
    selected = selector()
    selected.max_attempts = 2
    connect = Mock(side_effect=NetmikoAuthenticationException('Authentication failed.'))
    with pytest.raises(CredentialError, match='cooldown'):
        selected.run(connect)
    assert connect.call_count == 2
    with pytest.raises(CredentialError):
        selected.run(connect)
    assert connect.call_count == 2


def test_unavailable_shared_state_prevents_device_connection():
    store = Store()
    store.request_object = Mock(return_value={'error': 'Profile local missing on this poller'})
    connect = Mock()
    with pytest.raises(CredentialError, match='missing'):
        selector(store).run(connect)
    connect.assert_not_called()


def test_failed_writeback_closes_connection_and_does_not_allow_commands():
    selected = selector()
    selected.client.request_object = Mock(side_effect=[{'token': 'lease', 'profiles': ['primary']}, RuntimeError('offline')])
    with pytest.raises(RuntimeError, match='offline'):
        selected.run(lambda: 'connected')
    assert selected.selected is None
    with pytest.raises(CredentialError, match='recorded'):
        selected.run(lambda: pytest.fail('must not reconnect'))


def test_profiles_are_atomic_and_override_legacy_environment_and_aws(monkeypatch):
    monkeypatch.setenv('NET_CREDENTIAL_PROFILES', 'primary,legacy')
    for name in ('PRIMARY', 'LEGACY'):
        monkeypatch.setenv(f'NET_{name}_USER', name.lower())
        monkeypatch.setenv(f'NET_{name}_PASS', name + '-password')
    monkeypatch.setenv('NET_PASS', 'old-password')
    monkeypatch.setenv('NET_ENABLE', 'old-enable')
    resolved = resolve(username=None, password=None, secret=None, aws=object(), prompt=False)
    assert len(resolved.profiles) == 2
    assert resolved.profiles[0].secret == ''
    assert 'password' not in repr(resolved)
    assert 'password' not in repr(resolved.profiles)
    assert 'primary, legacy' in resolved.describe()
    override = resolve(username='explicit', password='value', secret=None, aws=None, prompt=False)
    assert not override.profiles


@pytest.mark.parametrize('names', ['primary,primary', 'primary,', 'PRIMARY', 'foo-bar', ''])
def test_invalid_profiles_fail_without_printing_values(monkeypatch, names):
    monkeypatch.setenv('NET_CREDENTIAL_PROFILES', names)
    with pytest.raises(CredentialError):
        load_profiles()


def test_incomplete_profile_is_a_deployment_error(monkeypatch):
    monkeypatch.setenv('NET_CREDENTIAL_PROFILES', 'primary')
    monkeypatch.setenv('NET_PRIMARY_PASS', 'sensitive')
    with pytest.raises(CredentialError, match='missing on this poller') as exc:
        load_profiles()
    assert 'sensitive' not in str(exc.value)


def test_shared_connection_path_is_per_host_and_keeps_options(monkeypatch):
    nr = init_from_records({'one': {'hostname': '192.0.2.1', 'platform': 'cisco_ios'},
                            'two': {'hostname': '192.0.2.2', 'platform': 'cisco_ios'}},
                           None, None, 'old-enable', None, 22, 2, 9)
    for host in nr.inventory.hosts.values():
        host.data['netbox_id'] = 7
    prepare(nr, Credentials(profiles=PROFILES), Store('legacy'))
    assert not missing_credentials(nr)
    monkeypatch.setattr(Host, 'get_connection', lambda host, *_: host.username)
    first, second = nr.inventory.hosts.values()
    assert first.get_connection('netmiko', nr.config) == 'user-two'
    assert second.username == 'user-one'
    extras = first.get_connection_parameters('netmiko').extras
    assert extras['conn_timeout'] == 9
    assert extras['secret'] == ''
    assert extras['allow_agent'] is False
    assert nr.inventory.defaults.connection_options['netmiko'].extras['secret'] == 'old-enable'


def test_profiles_require_shared_netbox_identity():
    nr = init_from_records({'one': {'hostname': '192.0.2.1'}}, None, None, None, None, 22, 1)
    with pytest.raises(CredentialError, match='NetBox device inventory'):
        prepare(nr, Credentials(profiles=PROFILES))


def test_remediation_children_disable_discovery_after_selection():
    from netops.upgrade.remediation import child_environment
    selected = selector()
    selected.run(lambda: True)
    env = child_environment(SimpleNamespace(device_credentials=selected.credentials()))
    assert env['NET_CREDENTIAL_PROFILES'] == ''
    assert env['NET_USER'] == 'user-one'
    assert env['NET_AWS_SECRET'] == ''


def test_wrapped_transport_timeout_is_not_an_auth_rejection():
    try:
        raise EOFError('socket closed')
    except EOFError as cause:
        error = NetmikoAuthenticationException('Authentication failed')
        error.__cause__ = cause
    assert not rejected(error)


def test_wrapped_partial_authentication_never_tries_more_accounts():
    from paramiko.ssh_exception import PartialAuthentication
    error = NetmikoAuthenticationException('Authentication to device failed')
    error.__context__ = PartialAuthentication(['keyboard-interactive'])
    assert not rejected(error)


def test_f5_client_refreshes_username_after_profile_selection(monkeypatch):
    from netops.f5_waf import Client
    nr = init_from_records({'one': {'hostname': '192.0.2.1', 'platform': 'f5_tmsh'}}, None, None, None, None, 22, 1)
    nr.inventory.hosts['one'].data['netbox_id'] = 7
    prepare(nr, Credentials(profiles=PROFILES), Store('legacy'))
    host = nr.inventory.hosts['one']
    client = Client(host)
    calls = []

    def request(method, path, payload):
        calls.append(payload)
        return {'token': {'token': 'session-token'}}

    monkeypatch.setattr(client, 'request', request)
    client.authenticate()
    assert calls[0]['username'] == 'user-two'
    assert calls[0]['password'] == 'password-two'
    client.authenticate()
    assert calls[1] == calls[0]
    assert len(host.selector.client.calls) == 2  # One shared selection, including writeback.
    client.session.close()


def test_validate_access_cli_records_profile_and_checks_enable(monkeypatch):
    from netops import cli
    nr = init_from_records({'one': {'hostname': '192.0.2.1', 'platform': 'cisco_ios'}}, None, None, None, None, 22, 1)
    nr.inventory.hosts['one'].data['netbox_id'] = 7
    credentials = Credentials(profiles=PROFILES)
    prepare(nr, credentials, Store('legacy'))
    monkeypatch.setattr(cli, '_connect', lambda *_: (nr, credentials, 0))
    connection = Mock()
    monkeypatch.setattr(Host, 'get_connection', lambda *_: connection)
    assert cli.main(['validate-access', '--netbox']) == 0
    connection.enable.assert_called_once()
    assert nr.inventory.hosts['one'].data['credential_profile'] == 'legacy'


def test_health_only_publishes_names_and_readiness(monkeypatch):
    from netops.credential_profiles import profile_health
    monkeypatch.setenv('NET_CREDENTIAL_PROFILES', 'primary')
    monkeypatch.setenv('NET_PRIMARY_USER', 'user-one')
    monkeypatch.setenv('NET_PRIMARY_PASS', 'password-one')
    health = profile_health()
    assert health['profiles'] == ['primary']
    assert health['status'] == 'ready'
    assert 'password-one' not in str(health)
    monkeypatch.delenv('NET_PRIMARY_PASS')
    assert profile_health()['status'] == 'incomplete'
