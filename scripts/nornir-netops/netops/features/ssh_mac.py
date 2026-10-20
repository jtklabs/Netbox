"""SSH MAC integrity policy. Encryption ciphers and key exchange are separate.

Changing a MAC allow-list removes algorithms even when its CLI is a positive
command. Remediation therefore requires replace mode; add mode reports drift.
"""
import re

from ..core import Desired, Entry, Feature, MODE_REPLACE, PlatformSupport, canonical_platform
from ..rollback import Reversal
from ..standards import of as standards_of
from ..ssh_mac_policy import validate_ssh_mac

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
    result = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line.startswith('ssh cipher integrity '):
            continue
        value = line[len('ssh cipher integrity '):]
        if value.startswith('custom '):
            algorithms = value[len('custom '):].strip('"').split(':')
            result.append(entry('integrity', ['custom', *algorithms], line))
        else:
            result.append(entry('integrity', [value], line))
    return result


def add_arguments(parser):
    parser.add_argument('--ios-scope', choices=('both', 'client', 'server'),
                        help='IOS SSH direction; defaults to ssh_mac.ios_scope or both')


def build_desired(args):
    settings = standards_of(args).section('ssh_mac')
    validate_ssh_mac(settings)
    override = args.ios_scope
    if override is not None and override not in ('both', 'client', 'server'):
        raise ValueError('ios_scope must be both, client or server')
    # Preserve the legacy defaults for existing standards and standalone use.
    policies = {}
    for platform in FEATURE.platforms.keys() | FEATURE.platform_runs.keys():
        local = settings.get('platforms', {}).get(platform, {})
        policy = {'macs': list(local.get('macs', settings.get('macs', [MAC])))}
        if platform == 'cisco_ios':
            policy['ios_scope'] = override or local.get('ios_scope', settings.get('ios_scope', 'both'))
        if platform == 'cisco_asa':
            policy = ({'macs': list(local['macs'])} if 'macs' in local else
                      {'asa_integrity': local.get('asa_integrity', settings.get('asa_integrity', 'high'))})
        policies[platform] = policy
    return Desired(keys=[], variables={'policies': policies})


def policy_for(platform, variables):
    return variables['policies'][platform]


def wanted_for(platform, variables):
    policy = policy_for(platform, variables)
    if platform == 'cisco_asa':
        values = ['custom', *policy['macs']] if 'macs' in policy else [policy['asa_integrity']]
        return [entry('integrity', values, '').key]
    scopes = ('server',)
    if platform == 'cisco_ios':
        scope = policy['ios_scope']
        scopes = ('client', 'server') if scope == 'both' else (scope,)
    return [entry(scope, policy['macs'], '').key for scope in scopes]


def per_device(keys, variables, host):
    return wanted_for(canonical_platform(host.platform), variables), variables


def plan(current, desired, mode, context):
    wanted = wanted_for(context['platform'], context['variables'])
    policy = policy_for(context['platform'], context['variables'])
    # Used by both the worker renderer and offline selftest after planning.
    context['variables']['macs'] = policy.get('macs', [])
    context['variables']['asa_integrity'] = (
        'custom ' + ':'.join(policy['macs']) if context['platform'] == 'cisco_asa' and 'macs' in policy
        else policy.get('asa_integrity'))
    # Compare the complete configured set; order alone does not cause drift.
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
