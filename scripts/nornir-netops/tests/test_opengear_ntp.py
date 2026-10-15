import shlex
from types import SimpleNamespace

from netops import opengear_ntp as og
from netops.core import MODE_ADD, MODE_REPLACE, canonical_platform
from netops.features.ntp import FEATURE
from test_ntp import parse_args


class Console:
    def __init__(self, enabled='on', server='192.0.2.9'):
        self.state = {'enabled':enabled,'server':server}
        self.calls = []
        self.fail = False
        self.ignore = False
        self.malformed = False

    def send_command(self, command, **kwargs):
        self.calls.append(command)
        words = shlex.split(command.split('; printf',1)[0])
        output = ''
        if words[1] == '-g':
            key = words[2]
            output = 'unrecognized' if self.malformed else key+' '+self.state[key.rsplit('.',1)[1]]
        elif words[1] == '-s' and not self.ignore and not self.fail:
            key,value = words[2].split('=',1)
            self.state[key.rsplit('.',1)[1]] = value
        return output+'\n'+og.MARKER+(':'+str(int(self.fail)))+'\n'


def run(box, *, dry=True, mode=MODE_REPLACE, region=False):
    host = SimpleNamespace(name='console1',platform='opengear_linux',data={'region':'east'} if region else {})
    def connect(driver, config):
        assert host.platform == 'linux'
        return box
    host.get_connection = connect
    task = SimpleNamespace(host=host,nornir=SimpleNamespace(config=None))
    variables = {'servers':['192.0.2.1','192.0.2.2']}
    if region:
        variables['regions'] = {'east':{'servers':['192.0.2.3','192.0.2.4'],'prefer':None,'region':'east'}}
    result = og.run(task,[],variables,mode,dry,True,True)
    assert host.platform == 'opengear_linux'
    return result


def test_platform_registration_and_shared_server_standard():
    assert canonical_platform('opengear') == 'opengear_linux'
    assert FEATURE.platform_runs['opengear_linux'] == og.run
    args = parse_args(['-s','192.0.2.1,192.0.2.2'])
    args.ntp_platforms = ['opengear']
    assert FEATURE.build_desired(args).variables['servers'] == ['192.0.2.1','192.0.2.2']


def test_preview_uses_regional_first_server_and_never_writes():
    box = Console()
    result = run(box,region=True)
    assert not result.failed and not result.changed
    assert result.result['desired'] == ['192.0.2.3']
    assert len(box.calls) == 2 and all(' -g ' in command for command in box.calls)


def test_apply_enables_replaces_verifies_and_is_idempotent():
    box = Console(enabled='off')
    result = run(box,dry=False)
    assert not result.failed and result.changed
    assert result.result['saved'] and result.result['verified']
    assert box.state == {'enabled':'on','server':'192.0.2.1'}
    assert len([c for c in box.calls if ' -r time' in c]) == 1
    again = run(box,dry=False)
    assert not again.failed and not again.changed and again.result['compliant']


def test_add_preserves_existing_server_but_can_initialize_or_enable():
    box = Console()
    result = run(box,dry=False,mode=MODE_ADD)
    assert result.failed and not result.changed and box.state['server'] == '192.0.2.9'
    box = Console(enabled='',server='')
    assert not run(box,dry=False,mode=MODE_ADD).failed
    box = Console(enabled='off',server='192.0.2.1')
    assert not run(box,dry=False,mode=MODE_ADD).failed
    assert box.state['enabled'] == 'on'


def test_malformed_or_denied_reads_never_write():
    for failure in ('malformed','fail'):
        box = Console()
        setattr(box,failure,True)
        result = run(box,dry=False)
        assert result.failed and not result.changed
        assert all(' -g ' in command for command in box.calls)


def test_ignored_write_fails_verification_and_records_original_state():
    box = Console()
    box.ignore = True
    result = run(box,dry=False)
    assert result.failed and result.changed and result.result['verified'] is False
    assert result.result['config_before']['server'] == '192.0.2.9'
    assert result.result['rollback_unsupported']
