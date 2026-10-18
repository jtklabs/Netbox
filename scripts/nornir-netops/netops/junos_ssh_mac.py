"""Junos SSH MAC allow-list with a private, confirmed commit."""
import re

from . import archive
from .core import MODE_REPLACE
from .junos_ntp import checked, commit, xml

SHOW = 'show configuration system services ssh | display inheritance | display xml groups'
MAC = 'hmac-sha2-512'


def parse(output):
    root = xml(output)
    configs = list(root.iter('configuration'))
    if len(configs) != 1:
        raise ValueError('Junos response must contain exactly one configuration')
    config = configs[0]
    protected = any(any(k in n.attrib for k in ('group', 'inactive', 'protect'))
                    for n in config.iter())

    def prune(parent):
        for child in list(parent):
            if 'inactive' in child.attrib:
                parent.remove(child)
            else:
                prune(child)
    prune(config)
    ssh = config.find('system/services/ssh')
    macs = [] if ssh is None else [node.text for node in ssh.findall('macs')]
    if any(not value or not re.fullmatch(r'[a-zA-Z0-9@._-]+', value) for value in macs):
        raise ValueError('Invalid Junos SSH MAC configuration')
    return {'macs': sorted(macs), 'enabled': ssh is not None, 'protected': protected}


def commands_for(before):
    if before['enabled'] and before['macs'] == [MAC]:
        return []
    # Junos leaf-lists append on set. Delete only this leaf in the private
    # candidate, then set the complete list before committing either change.
    return (['delete system services ssh macs'] if before['macs'] else []) + [
        'set system services ssh macs ' + MAC]


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result

    payload = dict(platform='juniper_junos', mode=mode, current=[], desired=[MAC],
                   add=[], remove=[], commands=[], compliant=False, advisories=[], notes=[],
                   rollback=[], rollback_unsupported=[], applied=False, saved=None,
                   verified=None, missing_after=[], save_command=None, skipped=False, skip_reason=None)
    connection = None
    locked = changed_candidate = confirmed_attempted = attempted = False
    stage = 'opening the SSH connection'
    try:
        connection = task.host.get_connection('netmiko', task.nornir.config)

        def read(command):
            nonlocal stage
            stage = 'reading SSH configuration' if command == SHOW else 'checking pending commits'
            return checked(connection.send_command(('run ' if locked else '') + command, read_timeout=60))

        before = parse(read(SHOW))
        commands = commands_for(before)
        payload.update(config_before=before, current=before['macs'], compliant=not commands)
        if commands and mode != MODE_REPLACE:
            payload['advisories'].append('SSH MAC integrity differs; use replace mode to change the algorithm allow-list.')
            return Result(host=task.host, result=payload, changed=False)
        payload.update(commands=commands, add=[c for c in commands if c.startswith('set ')],
                       remove=[c for c in commands if c.startswith('delete ')])
        if commands:
            payload['save_command'] = 'commit'
            payload['rollback_unsupported'] = ['After confirmation, restore the previous SSH MAC list through a reviewed Junos change.']
            if before['protected']:
                payload['advisories'].append('SSH has inherited, inactive or protected configuration; remediate its owning configuration manually.')
            if not dry_run:
                if not save or not verify:
                    raise ValueError('Junos SSH MAC remediation requires saving and verification')
                if before['protected']:
                    raise ValueError(payload['advisories'][0])
                if not before['enabled']:
                    raise ValueError('SSH service is not explicitly enabled; review its configuration before remediation')
                stage = 'entering private configuration mode'
                checked(connection.config_mode(config_command='configure private'))
                locked = True
                stage = 'checking candidate changes'
                if checked(connection.send_command('show | compare', read_timeout=60)).strip():
                    raise ValueError('Junos has pending candidate changes; no changes were made')
                if re.search(r'rollback in|commit confirmed|pending', read('show system commit'), re.I):
                    raise ValueError('Junos has a pending commit; resolve it before remediation')
                if parse(read(SHOW)) != before:
                    raise ValueError('Junos SSH configuration changed during planning; retry the job')
                stage = 'archiving the change plan'
                archive.checkpoint(task, payload)
                attempted = changed_candidate = True
                stage = 'sending SSH MAC configuration'
                checked(connection.send_config_set(commands, exit_config_mode=False))
                stage = 'running commit check'
                commit(connection, 'commit check')
                confirmed_attempted = True
                stage = 'running commit confirmed 5'
                commit(connection, 'commit confirmed 5')
                payload.update(applied=True, saved=False, verified=False)
                after = parse(read(SHOW))
                stage = 'verifying SSH MAC configuration'
                remaining = commands_for(after)
                payload.update(config_after=after, missing_after=remaining)
                if remaining:
                    raise ValueError('Junos SSH MAC verification failed; the unconfirmed commit will roll back')
                stage = 'confirming the permanent commit'
                commit(connection, 'commit')
                payload.update(saved=True, verified=True)
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        payload['failure_stage'] = stage
        if confirmed_attempted and not payload['saved']:
            payload['advisories'].append('Do not confirm this commit manually; an unconfirmed commit rolls back after five minutes.')
        detail = str(exc).strip() or 'no error detail returned'
        error = RuntimeError(f'Junos SSH MAC failed while {stage}: {type(exc).__name__}: {detail}')
        return Result(host=task.host, result=payload, changed=attempted, failed=True,
                      exception=error.with_traceback(exc.__traceback__))
    finally:
        if locked:
            try:
                if changed_candidate and not confirmed_attempted:
                    checked(connection.send_command('rollback 0', read_timeout=60))
                connection.exit_config_mode()
            except Exception:
                payload['advisories'].append('Could not close the Junos configuration session; review pending changes before retrying.')
