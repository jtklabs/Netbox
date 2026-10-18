"""SSH MAC integrity policy. Encryption ciphers and key exchange are separate.

Changing a MAC allow-list removes algorithms even when its CLI is a positive
command. Remediation therefore requires replace mode; add mode reports drift.
"""
import re

from ..core import Desired, Entry, Feature, MODE_REPLACE, PlatformSupport, canonical_platform
from ..rollback import Reversal
from ..standards import of as standards_of

MAC = 'hmac-sha2-512'


def entry(scope, algorithms, line):
    return Entry(key=scope + ':' + ','.join(sorted(set(algorithms))), line=line,
                 data={'scope': scope})


def parse_ios(output):
    result = []
    for raw in output.splitlines():
        match = re.fullmatch(r'ip ssh (client|server) algorithm mac (.+)', raw.strip())
        if match:
            result.append(entry(match[1], match[2].split(), raw.strip()))
    return result


def parse_eos(output):
    result, in_ssh = [], False
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line == '!':
            continue
        if not raw[0].isspace():
            in_ssh = line == 'management ssh'
        elif in_ssh and line.startswith('mac '):
            result.append(entry('server', line.split()[1:], line))
    return result


def parse_asa(output):
    return [entry('integrity', line.split()[3:], line)
            for raw in output.splitlines()
            if (line := raw.strip()).startswith('ssh cipher integrity ')]


def add_arguments(parser):
    parser.add_argument('--ios-scope', choices=('both', 'client', 'server'),
                        help='IOS SSH direction; defaults to ssh_mac.ios_scope or both')


def build_desired(args):
    settings = standards_of(args).section('ssh_mac')
    if set(settings) - {'macs', 'ios_scope', 'asa_integrity'}:
        raise ValueError('Unknown ssh_mac setting')
    if settings.get('macs', [MAC]) != [MAC]:
        raise ValueError('ssh_mac.macs must be [hmac-sha2-512]')
    if settings.get('asa_integrity', 'high') != 'high':
        raise ValueError('ssh_mac.asa_integrity must be high')
    scope = args.ios_scope or settings.get('ios_scope', 'both')
    if scope not in ('both', 'client', 'server'):
        raise ValueError('ssh_mac.ios_scope must be both, client or server')
    return Desired(keys=['server:' + MAC], variables={'ios_scope': scope})


def wanted_for(platform, variables):
    if platform == 'cisco_asa':
        return ['integrity:high']
    scopes = ('server',)
    if platform == 'cisco_ios':
        scope = variables['ios_scope']
        scopes = ('client', 'server') if scope == 'both' else (scope,)
    return [scope + ':' + MAC for scope in scopes]


def per_device(keys, variables, host):
    return wanted_for(canonical_platform(host.platform), variables), variables


def plan(current, desired, mode, context):
    wanted = wanted_for(context['platform'], context['variables'])
    # Equality of the complete list matters: SHA-512 alongside SHA-1 fails.
    missing = [key for key in wanted if [e.key for e in current
               if e.data['scope'] == key.split(':')[0]] != [key]]
    if missing and mode != MODE_REPLACE:
        context.setdefault('advisories', []).append(
            'SSH MAC integrity differs: ' + ', '.join(missing) +
            '. Use replace mode to change the algorithm allow-list.')
        return [], []
    return missing, []


def reverse(commands, current, removed, context):
    platform = context['platform']
    restores = []
    for key in context['added']:
        scope = key.split(':')[0]
        previous = [e.line for e in current if e.data['scope'] == scope]
        if platform == 'arista_eos':
            restores += ['management ssh', *(previous or ['no mac']), 'exit']
        elif platform == 'cisco_asa':
            restores += previous or ['no ssh cipher integrity']
        else:
            restores += previous or [f'default ip ssh {scope} algorithm mac']
    return Reversal(commands=restores)


# Junos uses its own candidate/commit transaction, never the IOS push pipeline.
from .. import junos_ssh_mac

FEATURE = Feature(
    name='ssh-mac', help='audit or enforce SSH MAC integrity (replace mode to apply)',
    platforms={
        'cisco_ios': PlatformSupport('show running-config all | include ^ip ssh', parse_ios,
                                    'ip ssh server algorithm mac hmac-sha1\n'),
        'arista_eos': PlatformSupport('show running-config all | section management ssh', parse_eos,
                                     'management ssh\n   mac hmac-sha1\n'),
        'cisco_asa': PlatformSupport('show running-config all ssh', parse_asa,
                                    'ssh cipher integrity medium\n'),
    },
    platform_runs={'juniper_junos': junos_ssh_mac.run},
    add_arguments=add_arguments, build_desired=build_desired,
    plan=plan, reverse=reverse, per_device=per_device, verify_with_plan=True,
)
