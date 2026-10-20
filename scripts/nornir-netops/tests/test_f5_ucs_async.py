import shlex
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from netops.upgrade import f5


def test_long_save_launches_once_and_waits_for_exit(monkeypatch):
    clock = [0]
    monkeypatch.setattr(f5.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(f5, '_sleep', lambda s: clock.__setitem__(0, clock[0] + s))
    client = SimpleNamespace(request=Mock(side_effect=[{'commandResult': s} for s in
        ['NETOPS_UCS_STARTED', 'NETOPS_UCS_RUNNING', 'NETOPS_UCS_RUNNING', 'NETOPS_UCS_EXIT=0']]))
    f5.save_ucs(client, 'backup.ucs', timeout=7200, poll=1000)
    assert clock[0] == 2000
    calls = client.request.call_args_list
    assert sum('nohup' in c.args[2]['utilCmdArgs'] for c in calls) == 1
    assert all(c.args[1] == '/mgmt/tm/util/bash' for c in calls)


@pytest.mark.parametrize('result', ['NETOPS_UCS_EXIT=1', 'NETOPS_UCS_EXIT=75', 'NETOPS_UCS_MISSING', 'garbage'])
def test_unknown_or_failed_completion_never_passes(result):
    client = SimpleNamespace(request=Mock(side_effect=[{'commandResult': 'NETOPS_UCS_STARTED'}, {'commandResult': result}]))
    with pytest.raises(RuntimeError, match='failed or completion is unknown'):
        f5.save_ucs(client, 'backup.ucs')


def test_ambiguous_launch_is_never_repeated():
    client = SimpleNamespace(request=Mock(side_effect=requests.Timeout()))
    with pytest.raises(requests.Timeout):
        f5.save_ucs(client, 'backup.ucs')
    assert client.request.call_count == 1


def test_deadline_does_not_relaunch(monkeypatch):
    clock = [0]
    monkeypatch.setattr(f5.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(f5, '_sleep', lambda s: clock.__setitem__(0, clock[0] + s))
    client = SimpleNamespace(request=Mock(side_effect=[{'commandResult': 'NETOPS_UCS_STARTED'},
                                                      {'commandResult': 'NETOPS_UCS_RUNNING'},
                                                      {'commandResult': 'NETOPS_UCS_RUNNING'}]))
    with pytest.raises(TimeoutError, match='may still be running'):
        f5.save_ucs(client, 'backup.ucs', timeout=5, poll=5)
    assert client.request.call_count == 3


def test_passphrase_is_shell_quoted_without_expansion():
    client = SimpleNamespace(request=Mock(side_effect=[{'commandResult': 'NETOPS_UCS_STARTED'}, {'commandResult': 'NETOPS_UCS_EXIT=0'}]))
    password = 'a$(id)`whoami`;"b\\c'
    f5.save_ucs(client, 'backup.ucs', password)
    outer = shlex.split(client.request.call_args_list[0].args[2]['utilCmdArgs'])[1]
    launch = shlex.split(outer)
    script = launch[launch.index('nohup') + 3]
    parts = shlex.split(script)
    command = parts[parts.index('/bin/bash') + 2].rstrip(';')
    words = shlex.split(command)
    assert words[:2] == ['tmsh', '-c'] and len(words) == 3
    assert '$(id)' in words[2] and '`whoami`' in words[2]


@pytest.mark.parametrize('name', ['../backup.ucs', 'backup.ucs;id', 'backup', '/var/local/ucs/backup.ucs', 'backup\n.ucs'])
def test_invalid_archive_name_never_launches(name):
    client = SimpleNamespace(request=Mock())
    with pytest.raises(ValueError, match='invalid UCS archive name'):
        f5.save_ucs(client, name)
    client.request.assert_not_called()


def test_control_character_passphrase_never_launches():
    client = SimpleNamespace(request=Mock())
    with pytest.raises(ValueError, match='control characters'):
        f5.save_ucs(client, 'backup.ucs', 'secret\nvalue')
    client.request.assert_not_called()


def test_unconfirmed_launch_never_polls_or_relaunches():
    client = SimpleNamespace(request=Mock(return_value={'commandResult': 'unknown'}))
    with pytest.raises(RuntimeError, match='launch not confirmed'):
        f5.save_ucs(client, 'backup.ucs')
    assert client.request.call_count == 1


def test_poll_transport_failure_never_relaunches():
    client = SimpleNamespace(request=Mock(side_effect=[{'commandResult': 'NETOPS_UCS_STARTED'}, requests.Timeout()]))
    with pytest.raises(requests.Timeout):
        f5.save_ucs(client, 'backup.ucs')
    assert client.request.call_count == 2
