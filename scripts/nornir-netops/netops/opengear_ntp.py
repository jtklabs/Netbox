"""Legacy CM7100/IM7200 NTP through the documented config CLI over SSH."""
import re
import shlex

from . import archive
from .core import MODE_REPLACE, normalize, validate_address

PREFIX = '/bin/config'
MARKER = '__NETOPS_OPENGEAR_STATUS__'


def execute(connection, command):
    """Check shell exit status; an empty response is not proof of success."""
    output = connection.send_command(command + "; printf '\\n" + MARKER + ":%s\\n' \"$?\"", read_timeout=60)
    match = re.search(r'\n' + re.escape(MARKER) + r':(\d+)\s*$', output or '')
    if not match or int(match[1]) != 0:
        raise ValueError('Opengear rejected the NTP command; check shell/config permissions')
    return output[:match.start()].strip()


def leaf(output, key):
    match = re.fullmatch(re.escape(key) + r'(?:[ \t]+([^\r\n]*))?', output.strip())
    if not match:
        raise ValueError('Opengear returned an unexpected NTP setting; no empty-state assumption is safe')
    return (match[1] or '').strip()


def read(connection):
    enabled = leaf(execute(connection, PREFIX + ' -g config.ntp.enabled'), 'config.ntp.enabled')
    server = leaf(execute(connection, PREFIX + ' -g config.ntp.server'), 'config.ntp.server')
    if enabled not in ('on', 'off', ''):
        raise ValueError('Unrecognized Opengear NTP enable setting')
    if server:
        server = normalize(validate_address(server))
    return {'enabled': enabled, 'server': server}


def commands(state):
    return [PREFIX + ' -s ' + shlex.quote('config.ntp.server=' + state['server']),
            PREFIX + ' -s ' + shlex.quote('config.ntp.enabled=' + state['enabled']),
            PREFIX + ' -r time']


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result
    from .features.ntp import server_settings
    payload = dict(platform='opengear_linux', mode=mode, current=[], desired=[], add=[], remove=[],
                   commands=[], compliant=False, advisories=[], notes=[], rollback=[],
                   rollback_unsupported=[], applied=False, saved=None, verified=None,
                   missing_after=[], save_command=None, skipped=False, skip_reason=None)
    original = task.host.platform
    attempted = False
    try:
        servers, _, regional = server_settings(variables, task.host)
        wanted = normalize(validate_address(servers[0]))
        after = {'enabled': 'on', 'server': wanted}
        payload['desired'] = [wanted]
        if regional:
            payload['region'] = regional['region']
        payload['notes'].append('Legacy Opengear uses the first regional/default NTP server. Timezone, authentication, source and routing settings are unchanged. Verification checks configuration, not clock synchronization.')
        task.host.platform = 'linux'
        connection = task.host.get_connection('netmiko', task.nornir.config)
        before = read(connection)
        payload.update(config_before=before, current=[before['server']] if before['server'] else [],
                       compliant=before == after)
        if not payload['compliant']:
            if before['server'] and before['server'] != wanted and mode != MODE_REPLACE:
                raise ValueError('Legacy Opengear has one NTP server setting; use replace mode to change the existing server')
            payload.update(commands=commands(after), add=[wanted] if before['server'] != wanted else [],
                           remove=[before['server']] if before['server'] and before['server'] != wanted else [])
            payload['rollback_unsupported'] = ['Automatic rollback is unavailable for the Opengear shell. Restore the recorded config_before server/enabled values with /bin/config -s, then /bin/config -r time.']
            if not dry_run:
                if not save or not verify:
                    raise ValueError('Opengear config writes persist immediately; saving and verification are required')
                if read(connection) != before:
                    raise ValueError('Opengear NTP changed during planning; retry the job')
                archive.checkpoint(task, payload)
                attempted = True
                for command in payload['commands']:
                    execute(connection, command)
                payload['applied'] = payload['saved'] = True
                observed = read(connection)
                payload.update(config_after=observed, verified=observed == after)
                if not payload['verified']:
                    payload['missing_after'] = ['Opengear NTP configuration differs after write']
                    raise ValueError(payload['missing_after'][0])
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        return Result(host=task.host, result=payload, changed=attempted, failed=True, exception=exc)
    finally:
        task.host.platform = original
