"""IPv4 NTP on Junos SRX/MX, with a private, confirmed commit over SSH."""

import ipaddress
import re
import xml.etree.ElementTree as ET

from . import archive
from .core import MODE_REPLACE, scrub, validate_word
from .debuglog import protect

SHOW = 'show configuration system ntp | display inheritance | display xml groups'
INTERFACES = 'show interfaces terse | display xml'


def xml(output):
    if '<!DOCTYPE' in output.upper() or '<!ENTITY' in output.upper():
        raise ValueError('Unexpected Junos XML declaration')
    try:
        root = ET.fromstring(output.strip())
    except ET.ParseError:
        raise ValueError('Junos returned invalid XML; check command permissions') from None
    for node in root.iter():
        node.tag = node.tag.rsplit('}', 1)[-1]
        node.attrib = {key.rsplit('}', 1)[-1]: value for key, value in node.attrib.items()}
        if node.tag in ('rpc-error', 'error', 'error-message'):
            raise ValueError('Junos rejected the read command')
    return root


def ipv4(value):
    try:
        return str(ipaddress.IPv4Address(value))
    except (ValueError, TypeError):
        raise ValueError('Junos NTP requires IPv4 literals') from None


def key_id(value):
    if not isinstance(value, str) or not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise ValueError('Junos NTP key ID must be from 1 to 65535')
    return str(int(value))


def parse(output):
    root = xml(output)
    configs = [node for node in root.iter('configuration')]
    if len(configs) != 1:
        raise ValueError('Junos response must contain exactly one configuration')
    state = dict(servers={}, sources={}, keys={}, trusted=[], protected=False)
    for node in configs[0].iter():
        if any(key in node.attrib for key in ('group', 'inactive', 'protect')):
            state['protected'] = True
    ntp = configs[0].find('system/ntp')
    if ntp is None:
        return state
    # Inactive config is not effective state, and is never activated implicitly.
    def prune(parent):
        for child in list(parent):
            if 'inactive' in child.attrib:
                parent.remove(child)
            else:
                prune(child)
    prune(configs[0])
    ntp = configs[0].find('system/ntp')
    if ntp is None:
        return state
    for node in ntp:
        name = node.findtext('name')
        if node.tag == 'server':
            address = ipv4(name)
            if address in state['servers']:
                raise ValueError('Duplicate Junos NTP server')
            if node.find('nts') is not None:
                state['protected'] = True
            state['servers'][address] = {
                'key': key_id(node.findtext('key')) if node.find('key') is not None else None,
                'prefer': node.find('prefer') is not None,
                'vrf': validate_word(node.findtext('routing-instance'), 'NTP routing instance')
                if node.find('routing-instance') is not None else None,
                'version': node.findtext('version'),
            }
        elif node.tag == 'source-address':
            address = ipv4(name)
            vrf = node.findtext('routing-instance') or 'default'
            if vrf in state['sources']:
                raise ValueError('Multiple Junos NTP source addresses in one routing instance')
            state['sources'][validate_word(vrf, 'NTP routing instance')] = address
        elif node.tag == 'authentication-key':
            material = node.findtext('value')
            if material:
                protect([material])
            if not material or not node.findtext('type'):
                raise ValueError('Incomplete Junos NTP authentication key')
            state['keys'][key_id(name)] = node.findtext('type')
        elif node.tag == 'trusted-key':
            state['trusted'].append(key_id(node.text))
    return state


def interface_addresses(output):
    root = xml(output)
    if not any(node.tag == 'interface-information' for node in root.iter()):
        raise ValueError('Junos returned no interface information')
    result = {}
    for interface in root.iter('logical-interface'):
        name = interface.findtext('name')
        for family in interface.findall('address-family'):
            if family.findtext('address-family-name') == 'inet':
                for address in family.findall('interface-address'):
                    local = address.findtext('ifa-local')
                    if local:
                        result.setdefault(name, set()).add(ipv4(local.split('/')[0]))
    return result


def source_address(source, output):
    if not re.fullmatch(r'[a-z][a-z0-9/-]*(?::[0-9]+)?\.[0-9]+', source):
        raise ValueError('Junos service-source must select a logical interface, such as lo0.0 or fxp0.0')
    addresses = interface_addresses(output).get(source, set())
    if len(addresses) != 1:
        raise ValueError('Junos source interface must have exactly one IPv4 address; ambiguous selections require review')
    return next(iter(addresses))


def plan(before, variables, mode, *, verification=False):
    wanted = [ipv4(server) for server in variables['servers']]
    vrf = variables.get('vrf')
    vrf = validate_word(vrf, 'NTP routing instance') if vrf else None
    prefer = variables.get('prefer')
    auth = [entry for entry in variables['entries'].values() if entry['kind'] == 'key']
    selected_key = key_id(str(variables['key_id'])) if variables.get('key_id') else None
    commands = []
    for entry in auth:
        number = key_id(entry['id'])
        algorithm = entry['type']
        if algorithm not in ('md5', 'sha1', 'sha256'):
            raise ValueError('Junos NTP supports md5, sha1 or sha256 authentication')
        material = entry['material']
        # Junos quoted strings have their own escaping rules; reject rather than
        # reinterpret key material or accidentally terminate a CLI statement.
        if any(c in material for c in ('"', '\\', '\n', '\r')):
            raise ValueError('Junos NTP key material cannot contain quotes, backslashes or newlines')
        if before['keys'].get(number) != algorithm or (variables.get('rewrite_keys') and not verification):
            commands.append(f'set system ntp authentication-key {number} type {algorithm} value "{material}"')
    trusted = {key_id(entry['id']) for entry in variables['entries'].values() if entry['kind'] == 'trusted-key'}
    for number in sorted(trusted - set(before['trusted'])):
        commands.append(f'set system ntp trusted-key {number}')
    for address in wanted:
        current = before['servers'].get(address)
        base = f'system ntp server {address}'
        if current is None:
            commands.append('set ' + base)
            current = dict(key=None, prefer=False, vrf=None)
        for leaf, field, value in (('key', 'key', selected_key), ('prefer', 'prefer', address == prefer),
                                   ('routing-instance', 'vrf', vrf)):
            if current[field] == value:
                continue
            if value:
                commands.append(f'set {base} {leaf}' + ('' if value is True else f' {value}'))
            else:
                commands.append(f'delete {base} {leaf}')
    if variables.get('source_address'):
        scope = vrf or 'default'
        address = variables['source_address']
        if any(other != scope and configured == address for other, configured in before['sources'].items()):
            raise ValueError('Selected Junos NTP source address belongs to another routing instance')
        if before['sources'].get(scope) != address:
            if scope in before['sources']:
                commands.append(f'delete system ntp source-address {before["sources"][scope]}')
            commands.append(f'set system ntp source-address {address}' + (f' routing-instance {vrf}' if vrf else ''))
    if mode == MODE_REPLACE:
        for address in before['servers']:
            if address not in wanted:
                commands.append(f'delete system ntp server {address}')
        if variables.get('manages_auth'):
            # Never break peer/broadcast authentication by pruning unrelated keys.
            # The managed key and trust are reconciled; other key IDs stay intact.
            if selected_key not in trusted and selected_key in before['trusted']:
                commands.append(f'delete system ntp trusted-key {selected_key}')
    return commands


def observe(read):
    before = parse(read(SHOW))
    servers = [{'server': server, 'vrf': row['vrf'] or 'default'} for server, row in before['servers'].items()]
    if not servers:
        return dict(status='unconfigured', reason='No NTP servers configured', servers=[])
    scopes = {row['vrf'] for row in servers}
    if len(scopes) != 1 or not before['sources'].get(next(iter(scopes))):
        return dict(status='ambiguous', reason='NTP servers need one explicit source address and routing instance', servers=servers)
    vrf = scopes.pop()
    address = before['sources'][vrf]
    interfaces = interface_addresses(read(INTERFACES))
    matches = [name for name, addresses in interfaces.items() if address in addresses]
    if len(matches) != 1 or len(interfaces[matches[0]]) != 1:
        return dict(status='ambiguous', reason='NTP source address does not identify exactly one logical interface', servers=servers)
    return dict(status='resolved', source=matches[0], source_address=address, vrf=vrf, servers=servers)


def checked(output):
    if re.search(r'(?im)^\s*(?:error:|syntax error|unknown command|permission denied)', output or ''):
        raise ValueError('Junos rejected the operation; review device permissions and configuration')
    return output or ''


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result
    from .features.ntp import per_device

    payload = dict(platform='juniper_junos', mode=mode, current=[], desired=[], add=[], remove=[],
                   commands=[], compliant=False, advisories=[], notes=[], rollback=[],
                   rollback_unsupported=[], applied=False, saved=None, verified=None,
                   missing_after=[], save_command=None, skipped=False, skip_reason=None)
    connection = None
    locked = changed_candidate = confirmed_attempted = attempted = False
    secrets = [entry['material'] for entry in variables['entries'].values() if entry['kind'] == 'key']
    protect(secrets)
    try:
        _, variables = per_device(list(desired), dict(variables), task.host)
        # Validate desired values before opening a connection.
        plan(dict(servers={}, sources={}, keys={}, trusted=[]), variables, mode)
        connection = task.host.get_connection('netmiko', task.nornir.config)

        def read(command):
            return checked(connection.send_command(('run ' if locked else '') + command, read_timeout=60))

        if variables.get('source'):
            variables['source_address'] = source_address(variables['source'], read(INTERFACES))
        before = parse(read(SHOW))
        commands = plan(before, variables, mode)
        payload.update(config_before=before, current=list(before['servers']), desired=variables['servers'],
                       commands=[scrub(c, secrets) for c in commands], compliant=not commands,
                       add=[scrub(c, secrets) for c in commands if c.startswith('set ')],
                       remove=[c for c in commands if c.startswith('delete ')])
        payload['notes'].append('Audits configured NTP settings, not live synchronization. Unselected sources and unrelated authentication keys are preserved.')
        if variables.get('region'):
            payload['notes'].append(f'NTP servers for region {variables["region"]}')
        if variables.get('source_address'):
            payload['notes'].append(f'Source {variables["source"]}: {variables["source_address"]}')
        if commands:
            payload['save_command'] = 'commit'
            payload['rollback_unsupported'] = ['After a successful confirmed commit, manual Junos rollback requires a reviewed change; encrypted key material is not archived.']
            if before['protected']:
                payload['advisories'].append('NTP has inherited, inactive, protected or NTS configuration; remediate its owning configuration manually')
            if not dry_run:
                if not save or not verify:
                    raise ValueError('Junos NTP remediation requires saving and verification')
                if before['protected']:
                    raise ValueError(payload['advisories'][0])
                checked(connection.config_mode(config_command='configure private'))
                locked = True
                if checked(connection.send_command('show | compare', read_timeout=60)).strip():
                    raise ValueError('Junos has pending candidate changes; no changes were made')
                commits = read('show system commit')
                if re.search(r'rollback in|commit confirmed|pending', commits, re.I):
                    raise ValueError('Junos has a pending commit; resolve it before remediation')
                if parse(read(SHOW)) != before:
                    raise ValueError('Junos NTP changed during planning; retry the job')
                if variables.get('source') and source_address(variables['source'], read(INTERFACES)) != variables['source_address']:
                    raise ValueError('Junos source address changed during planning; retry the job')
                archive.checkpoint(task, payload)
                attempted = changed_candidate = True
                checked(connection.send_config_set(commands, exit_config_mode=False))
                output = checked(connection.commit(check=True))
                if 'configuration check succeeds' not in output:
                    raise ValueError('Junos did not confirm successful commit check')
                confirmed_attempted = True
                output = checked(connection.commit(confirm=True, confirm_delay=5))
                if 'commit complete' not in output:
                    raise ValueError('Junos did not confirm the temporary commit')
                payload.update(applied=True, saved=False, verified=False)
                after = parse(read(SHOW))
                payload['config_after'] = after
                remaining = plan(after, variables, mode, verification=True)
                payload['missing_after'] = [scrub(c, secrets) for c in remaining]
                if remaining:
                    raise ValueError('Junos NTP verification failed; the unconfirmed commit will roll back')
                checked_output = checked(connection.commit())
                if 'commit complete' not in checked_output:
                    raise ValueError('Junos did not confirm the permanent commit')
                payload.update(saved=True, verified=True)
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        if confirmed_attempted and not payload['saved']:
            payload['advisories'].append('Commit outcome requires review. Do not confirm it manually; an unconfirmed commit rolls back after five minutes.')
        return Result(host=task.host, result=payload, changed=attempted, failed=True,
                      exception=RuntimeError(scrub(str(exc), secrets)))
    finally:
        if locked:
            try:
                # Discard only our private candidate, never the shared candidate
                # or another operator's uncommitted work.
                if changed_candidate and not confirmed_attempted:
                    checked(connection.send_command('rollback 0', read_timeout=60))
                connection.exit_config_mode()
            except Exception:
                payload['advisories'].append('Could not cleanly close the Junos configuration session; review pending changes before retrying')
