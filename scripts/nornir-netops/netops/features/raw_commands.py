"""Literal, ordered configuration commands with explicit compliance checks."""
import re

from .. import archive
from ..core import Desired, Feature, MODE_REPLACE, SAVE_COMMANDS, canonical_platform
from ..raw_commands_policy import validate_raw_commands
from ..standards import of as standards_of

SHOW = {
    'cisco_ios': 'show running-config',
    'arista_eos': 'show running-config',
    'cisco_asa': 'show running-config',
    'cisco_nxos': 'show running-config',
    'juniper_junos': 'show configuration | display set',
}


def build_desired(args):
    settings = standards_of(args).section('raw_commands')
    if not settings and getattr(args, '_selftest', False):
        return Desired(keys=[], variables={'raw_commands': None})
    validate_raw_commands(settings)
    return Desired(keys=[], variables={'raw_commands': settings})


def select(settings, host):
    if not settings['enabled']:
        raise ValueError('raw_commands.enabled is false; raw command execution is disabled')
    platform = canonical_platform(host.platform)
    policy = settings['platforms'].get(platform)
    if policy is None:
        raise ValueError(f'No raw command standard is defined for platform {platform!r}')
    data = getattr(host, 'data', {}) or {}
    chain = data.get('regions') or ([data['region']] if data.get('region') else [])
    for region in chain:
        slug = str(region).strip().lower()
        if slug in policy.get('regions', {}):
            return policy['regions'][slug], slug
    if 'commands' not in policy:
        raise ValueError(f'No raw command region matches {host.name} and no platform default is defined')
    return {key: value for key, value in policy.items() if key != 'regions'}, None


def checked_cli(command, output):
    from ..runner import _check_understood
    _check_understood(command, output)
    if re.search(r'(?im)^\s*(?:%\s*)?(?:error:|syntax error|invalid command|permission denied)', output):
        raise ValueError(f'The device rejected {command}')
    return output


def checked_nios(command, output):
    checked_cli(command, output)
    if re.search(r'(?im)^\s*(?:error\b|invalid\b|unknown command\b|syntax error\b|'
                 r'usage:|command not found\b|not authorized\b|permission denied\b)', output):
        raise ValueError(f'The device rejected {command}')
    return output


def missing(output, policy):
    # Exact lines ignore indentation; rejected fragments match anywhere in the
    # selected show output. Regex matching is explicit and uses line anchors.
    lines = {line.strip() for line in output.splitlines()}
    contains_failures = []
    for item in policy.get('contains', []):
        if isinstance(item, str):
            if item not in output:
                contains_failures.append(f'missing text: {item}')
        elif re.search(item['regex'], output, re.MULTILINE) is None:
            contains_failures.append(f"missing regex: {item['regex']}")
    return ([f'missing line: {line}' for line in policy.get('expect', []) if line.strip() not in lines]
            + contains_failures
            + [f'forbidden text: {text}' for text in policy.get('reject', []) if text in output])


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result
    from .. import runner
    from ..junos_ntp import checked, commit

    platform = canonical_platform(task.host.platform)
    payload = dict(platform=platform, mode=mode, current=[], desired=[], add=[], remove=[],
                   commands=[], compliant=False, advisories=[], notes=[], rollback=[],
                   rollback_unsupported=[], applied=False, saved=None, verified=None,
                   missing_after=[], save_command=None, skipped=False, skip_reason=None)
    connection = None
    nios = platform == 'infoblox_nios'
    locked = candidate = confirmed = attempted = False
    try:
        policy, region = select(variables['raw_commands'], task.host)
        show = policy.get('show') or SHOW.get(platform)
        if not show:
            raise ValueError('Infoblox NIOS requires an explicit show command')
        payload.update(desired=list(policy.get('expect', [])) + [
            ('contains: ' + item if isinstance(item, str) else 'contains regex: ' + item['regex'])
            for item in policy.get('contains', [])], selected_region=region,
            read_steps=[{'transport': 'ssh', 'command': show, 'purpose': 'read_config'}])
        junos = platform == 'juniper_junos'
        if nios:
            # NIOS has no dedicated Netmiko driver. Preserve the inventory
            # platform for reports and later features, as command collection does.
            original = task.host.platform
            task.host.platform = 'generic'
            try:
                connection = task.host.get_connection('netmiko', task.nornir.config)
            finally:
                task.host.platform = original
        elif junos:
            connection = task.host.get_connection('netmiko', task.nornir.config)

        def read(command=show):
            if nios:
                output = checked_nios(command, connection.send_command(command, read_timeout=60) or '')
            elif junos:
                output = checked(connection.send_command(('run ' if locked else '') + command, read_timeout=60))
            else:
                result = task.run(task=runner.netmiko_send_command, name=command,
                                  command_string=command, enable=True, read_timeout=60)
                output = result.result or ''
                checked_cli(command, output)
            if not output.strip():
                raise ValueError('Empty show response; cannot evaluate raw command checks')
            return output

        before = read()
        drift = missing(before, policy)
        # Archive the check evidence, not an entire configuration with unrelated secrets.
        payload.update(current=drift, config_before={'show_command': show, 'failed_checks': drift}, compliant=not drift)
        if not drift:
            return Result(host=task.host, result=payload, changed=False)
        if mode != MODE_REPLACE:
            payload['advisories'].append('Raw commands differ; replace mode is required to execute the command bundle.')
            return Result(host=task.host, result=payload, changed=False)
        commands = list(policy['commands'])
        payload.update(commands=commands, add=commands,
                       save_command=('commit' if junos else SAVE_COMMANDS.get(platform)) if save else None,
                       rollback_unsupported=['Raw command bundles have no automatically generated rollback.'])
        if nios:
            payload['notes'].append('NIOS set commands take effect directly; no separate save or commit is sent. '
                                    'Verification cannot undo commands already accepted by the appliance.')
        if dry_run:
            return Result(host=task.host, result=payload, changed=False)
        if not verify or (junos and not save):
            raise ValueError('Raw commands require verification; Junos also requires saving')
        if junos:
            # Operational show output can change on its own. Keep the candidate
            # race check anchored to configuration, independently of audit data.
            config_before = before if show == SHOW[platform] else read(SHOW[platform])
            checked(connection.config_mode(config_command='configure private'))
            locked = True
            if checked(connection.send_command('show | compare', read_timeout=60)).strip():
                raise ValueError('Junos has pending candidate changes')
            if re.search(r'rollback in|commit confirmed|pending', checked(connection.send_command(
                    'run show system commit', read_timeout=60)), re.I):
                raise ValueError('Junos has a pending commit')
            if read(SHOW[platform]) != config_before:
                raise ValueError('Junos configuration changed during planning; retry the job')
        archive.checkpoint(task, payload)
        attempted = True
        payload.update(saved=False if save and not nios else None, verified=False)
        if nios:
            for command in commands:
                checked_nios(command, connection.send_command(command, read_timeout=60) or '')
                payload['applied'] = True
        elif junos:
            candidate = True
            checked(connection.send_config_set(commands, exit_config_mode=False))
            commit(connection, 'commit check')
            confirmed = True
            commit(connection, 'commit confirmed 5')
        else:
            result = task.run(task=runner.netmiko_send_config, name='configure raw-commands',
                              config_commands=commands)
            checked_cli('raw commands', result.result or '')
        payload['applied'] = True
        remaining = missing(read(), policy)
        payload.update(config_after={'show_command': show, 'failed_checks': remaining}, missing_after=remaining, verified=not remaining)
        if remaining:
            if nios:
                raise ValueError('Raw command verification failed; NIOS commands already accepted may remain active')
            raise ValueError('Raw command verification failed; configuration was not saved or confirmed')
        if save and not nios:
            if junos:
                commit(connection, 'commit')
            else:
                command = SAVE_COMMANDS[platform]
                result = task.run(task=runner.netmiko_send_command, name=command,
                                  command_string=command, enable=True, read_timeout=120)
                checked_cli(command, result.result or '')
            payload['saved'] = True
        return Result(host=task.host, result=payload, changed=True)
    except Exception as exc:
        if confirmed and not payload['saved']:
            payload['advisories'].append('The unconfirmed Junos commit will roll back after five minutes; do not confirm it manually.')
        return Result(host=task.host, result=payload, changed=attempted, failed=True, exception=exc)
    finally:
        if nios and connection is not None:
            try:
                task.host.close_connection('netmiko')
            except Exception:
                payload['advisories'].append('Could not close the Infoblox SSH session.')
        if locked:
            try:
                if candidate and not confirmed:
                    checked(connection.send_command('rollback 0', read_timeout=60))
                connection.exit_config_mode()
            except Exception:
                payload['advisories'].append('Could not close the Junos configuration session; review pending changes.')


def selftest(desired):
    if desired.variables['raw_commands'] is None:
        print('No raw_commands section defined; skipping optional raw command checks.')
        return 0
    validate_raw_commands(desired.variables['raw_commands'])
    print('Raw command schema validated; commands are literal and require device readback checks.')
    return 0


FEATURE = Feature(name='raw-commands', help='audit or execute YAML command bundles by platform and region',
                  platforms={}, add_arguments=lambda parser: None, build_desired=build_desired,
                  run=run, reversible=False, selftest=selftest)
