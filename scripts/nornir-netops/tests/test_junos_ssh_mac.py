from types import SimpleNamespace

import pytest
from nornir.core.inventory import Host

from netops import junos_ssh_mac as junos


def config(macs, attributes='', enabled=True):
    ssh = (f'<ssh {attributes}>' + ''.join(f'<macs>{mac}</macs>' for mac in macs) + '</ssh>') if enabled else ''
    return '<configuration><system><services>' + ssh + '</services></system></configuration>'


class Device:
    def __init__(self, failure=None):
        self.macs = ['hmac-sha1', 'hmac-sha2-512']
        self.failure = failure
        self.calls = []
        self.attributes = ''

    def send_command(self, command, **kwargs):
        self.calls.append(command)
        if command.removeprefix('run ') == junos.SHOW:
            return config(self.macs, self.attributes)
        if command == 'show | compare':
            return 'pending changes' if self.failure == 'dirty' else ''
        if command == 'run show system commit':
            return 'rollback in 4mins' if self.failure == 'pending' else ''
        if command == 'rollback 0':
            return ''
        if command.startswith('commit'):
            assert kwargs['expect_string'] == r'(?m)^operator@srx\#' + r'[ \t]*$'
            assert kwargs['read_timeout'] == 120
            if command == 'commit check':
                return 'error: failed' if self.failure == 'check' else 'configuration check succeeds'
            if command == 'commit confirmed 5':
                if self.failure == 'commit':
                    raise EOFError()
                if self.failure != 'verify':
                    self.macs = ['hmac-sha2-512']
            # Dual-RE output can omit the optional rollback warning.
            return 're0:\nconfiguration check succeeds\ncommit complete\nre1:\ncommit complete'
        raise AssertionError(command)

    def config_mode(self, config_command):
        self.calls.append(config_command)
        return ''

    def send_config_set(self, commands, **kwargs):
        assert kwargs == {'exit_config_mode': False}
        self.calls.extend(commands)
        assert commands == ['delete system services ssh macs', 'set system services ssh macs hmac-sha2-512']
        return 'error: rejected' if self.failure == 'write' else ''

    def find_prompt(self):
        return 'operator@srx#'

    def exit_config_mode(self):
        self.calls.append('exit')


def run(box, monkeypatch, *, mode='replace', dry_run=False, save=True, verify=True):
    monkeypatch.setattr(Host, 'get_connection', lambda *args: box)
    task = SimpleNamespace(host=Host('srx', platform='juniper_junos'), nornir=SimpleNamespace(config=None))
    return junos.run(task, [], {}, mode, dry_run, save, verify)


def test_dry_run_and_add_do_not_enter_configuration(monkeypatch):
    box = Device()
    result = run(box, monkeypatch, dry_run=True)
    assert not result.failed and result.result['commands']
    assert box.calls == [junos.SHOW]
    box.calls.clear()
    result = run(box, monkeypatch, mode='add')
    assert result.result['advisories'] and not result.result['commands']
    assert box.calls == [junos.SHOW]


def test_confirmed_commit_verification_and_idempotence(monkeypatch):
    box = Device()
    result = run(box, monkeypatch)
    assert not result.failed and result.result['verified'] and result.result['saved']
    assert box.calls.index('commit confirmed 5') < box.calls.index('commit')
    box.calls.clear()
    assert run(box, monkeypatch).result['compliant']
    assert box.calls == [junos.SHOW]


@pytest.mark.parametrize('failure', ['dirty', 'pending', 'write', 'check', 'commit', 'verify'])
def test_failed_transaction_is_never_confirmed(monkeypatch, failure):
    box = Device(failure)
    result = run(box, monkeypatch)
    assert result.failed
    assert 'commit' not in box.calls
    if failure == 'commit':
        assert 'EOFError: no error detail returned' in str(result.exception)
        assert result.result['failure_stage'] == 'running commit confirmed 5'
    if failure in ('write', 'check'):
        assert 'rollback 0' in box.calls
    if failure in ('commit', 'verify'):
        assert result.result['advisories']
        assert 'rollback 0' not in box.calls


@pytest.mark.parametrize('attribute', ['inactive="inactive"', 'protect="protect"',
                                     'xmlns:j="urn:junos" j:group="GLOBAL"'])
def test_inherited_or_inactive_configuration_blocks_writes(monkeypatch, attribute):
    box = Device()
    box.attributes = attribute
    assert run(box, monkeypatch).failed
    assert box.calls == [junos.SHOW]


@pytest.mark.parametrize('options', [{'save': False}, {'verify': False}])
def test_required_transaction_guards(monkeypatch, options):
    box = Device()
    assert run(box, monkeypatch, **options).failed
    assert box.calls == [junos.SHOW]


@pytest.mark.parametrize('output', ['error: denied', '<rpc-reply/>', '<configuration>',
                                   '<!DOCTYPE foo><configuration/>', config(['bad\ncommand'])])
def test_invalid_read_is_not_treated_as_empty(output):
    with pytest.raises(ValueError):
        junos.parse(output)
