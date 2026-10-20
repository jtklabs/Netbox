"""Expired sessions recover once per rejected request, without replaying timeouts."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from netops.errors import DeviceHTTPError
from netops.f5_waf import Client, LOGIN
from netops.upgrade import f5, workflow
from test_upgrade_f5 import FakeBigIP


def response(status=200, body=None):
    return SimpleNamespace(status_code=status, text='response', json=lambda: body if body is not None else {})


@pytest.fixture
def client():
    result = Client(SimpleNamespace(hostname='192.0.2.10', username='operator', password='private'),
                    provider='radius')
    result.session.close()
    result.session = Mock(headers={'X-F5-Auth-Token': 'expired'})
    result.token = 'expired'
    return result


@pytest.mark.parametrize('method', ['GET', 'POST', 'PATCH', 'DELETE'])
def test_401_refreshes_without_old_header_and_replays_exact_request(client, method):
    calls = []
    replies = iter([response(401), response(body={'token': {'token': 'fresh'}}), response(body={'ok': True})])
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs, dict(client.session.headers)))
        return next(replies)
    client.session.request.side_effect = request
    payload = None if method == 'GET' else {'command': 'run'}
    assert client.request(method, '/mgmt/tm/example', payload, timeout=120) == {'ok': True}
    assert calls[0][:3] == calls[2][:3]
    assert calls[0][3]['X-F5-Auth-Token'] == 'expired'
    assert calls[1][0:2] == ('POST', client.base + LOGIN)
    assert 'X-F5-Auth-Token' not in calls[1][3]
    assert calls[1][2]['json'] == {'username': 'operator', 'password': 'private', 'loginProviderName': 'radius'}
    assert calls[2][3]['X-F5-Auth-Token'] == client.token == 'fresh'
    client.__exit__(None, None, None)
    assert client.session.delete.call_args.args[0].endswith('/tokens/fresh')


@pytest.mark.parametrize('status', [401, 403])
def test_refresh_login_rejection_stops_without_replay(client, status):
    client.session.request.side_effect = [response(401), response(status)]
    with pytest.raises(DeviceHTTPError) as caught:
        client.get_json('/mgmt/tm/asm/policies')
    assert caught.value.status_code == status
    assert client.session.request.call_count == 2
    assert client.token is None and 'X-F5-Auth-Token' not in client.session.headers
    assert 'private' not in str(caught.value) and 'expired' not in str(caught.value)


def test_second_401_does_not_loop(client):
    client.session.request.side_effect = [response(401), response(body={'token': {'token': 'fresh'}}), response(401)]
    with pytest.raises(DeviceHTTPError, match='401'):
        client.get_json('/mgmt/tm/asm/policies')
    assert client.session.request.call_count == 3


@pytest.mark.parametrize('status', [403, 500])
def test_other_errors_do_not_trigger_login(client, status):
    client.session.request.return_value = response(status)
    with pytest.raises(RuntimeError):
        client.get_json('/mgmt/tm/asm/policies')
    client.session.request.assert_called_once()


@pytest.mark.parametrize('error', [requests.ReadTimeout('late'), requests.ConnectionError('lost')])
def test_ambiguous_transport_failure_never_retries(client, error):
    client.session.request.side_effect = error
    with pytest.raises(type(error)):
        client.request('POST', '/mgmt/tm/sys', {'command': 'reboot', 'volume': 'HD1.2'})
    client.session.request.assert_called_once()


def test_initial_login_401_does_not_recurse(client):
    client.session.request.return_value = response(401)
    with pytest.raises(DeviceHTTPError):
        client.__enter__()
    client.session.request.assert_called_once()
    client.session.close.assert_called_once()


@pytest.mark.parametrize('error', [requests.ReadTimeout('private'), requests.ConnectionError('private')])
def test_refresh_transport_failure_is_not_an_ambiguous_reboot(client, error):
    client.session.request.side_effect = [response(401), error]
    with pytest.raises(RuntimeError, match='session reauthentication failed') as caught:
        f5.reboot_to(client, 'HD1.2')
    assert client.session.request.call_count == 2
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('body', [{}, {'token': None}, {'token': {'token': ''}}])
def test_malformed_refresh_cannot_replay_request(client, body):
    client.session.request.side_effect = [response(401), response(body=body)]
    with pytest.raises(ValueError, match='authentication token'):
        client.get_json('/mgmt/tm/asm/policies')
    assert client.session.request.call_count == 2 and client.token is None


def test_postcheck_recovers_policy_and_readiness_queries_across_expirations(client):
    backend = FakeBigIP()
    expirations = {f5.ASM_POLICIES, '/mgmt/tm/util/bash'}
    logins = []
    def request(method, url, json=None, **kwargs):
        path = url.removeprefix(client.base)
        if path == LOGIN:
            logins.append(json)
            return response(body={'token': {'token': f'fresh-{len(logins)}'}})
        if path in expirations:
            expirations.remove(path)
            return response(401)
        if path == '/mgmt/tm/sys/provision':
            return response(body={'items': [{'name': 'asm', 'level': 'nominal'}]})
        if path == f5.ASM_POLICIES:
            return response(body={'items': [{'fullPath': '/Common/parent', 'type': 'parent'},
                                            {'fullPath': '/Common/security', 'type': 'security'}]})
        if path == '/mgmt/tm/util/bash':
            return response(body={'commandResult': '1'})
        return response(body=backend.request(method, path, json))
    client.session.request.side_effect = request
    snapshot = f5.collect(client, lambda _: None)
    assert snapshot['errors'] == {}
    assert snapshot['waf'] == {'provisioned': True, 'ready': True, 'policy_count': 2}
    assert len(logins) == 2


@pytest.mark.parametrize('failure', ['login_401', 'request_401', 'login_timeout', 'permanent_401'])
def test_postvalidation_retries_authentication_until_ready_or_profile_deadline(client, monkeypatch, failure):
    clock = [0]
    backend = FakeBigIP()
    baseline = f5.collect(backend, lambda _: None)
    logins = []
    def request(method, url, json=None, **kwargs):
        path = url.removeprefix(client.base)
        starting = clock[0] < 90 or failure == 'permanent_401'
        if path == LOGIN:
            logins.append(clock[0])
            if starting and failure != 'request_401':
                if failure == 'login_timeout':
                    raise requests.ReadTimeout('private')
                return response(401)
            return response(body={'token': {'token': f'fresh-{len(logins)}'}})
        if starting or not client.token or client.token == 'expired':
            return response(401)
        return response(body=backend.request(method, path, json))
    client.session.request.side_effect = request
    monkeypatch.setattr(workflow.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(workflow.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(workflow, 'comparison_report', lambda *args: ({}, None))
    emit = Mock()
    plan = {'approved_profile': {'validation_timeout': 150}}
    options = SimpleNamespace(validation_timeout=30, poll_interval=30)
    result = workflow.converge(emit, options, baseline, plan, Mock(), Mock(),
                              lambda: f5.collect(client, lambda _: None), lambda _: [])
    summaries = [call.args[2]['progress_summary'] for call in emit.call_args_list
                 if call.args[0] == 'validating']
    assert summaries[0]['consecutive_clean'] == 0
    assert summaries[0]['seconds_remaining'] == 150
    assert {0, 30, 60} <= set(logins)
    if failure == 'permanent_401':
        assert result.failed and clock[0] == 150
        assert emit.call_args.args[0] == 'validation_failed'
        assert all(summary['consecutive_clean'] == 0 for summary in summaries)
    else:
        assert not result.failed and clock[0] == 120
        assert [summary['consecutive_clean'] for summary in summaries[-2:]] == [1, 2]


def test_failed_refresh_can_authenticate_on_a_later_request(client):
    client.session.request.side_effect = [response(401), response(401), response(401),
                                         response(body={'token': {'token': 'fresh'}}), response(body={'items': []})]
    with pytest.raises(DeviceHTTPError):
        client.get_json('/mgmt/tm/asm/policies')
    assert client.token is None
    assert client.get_json('/mgmt/tm/asm/policies') == {'items': []}
    assert client.token == 'fresh'
