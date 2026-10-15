"""IPv4 NTP on Junos SRX/MX, with a private, confirmed commit over SSH."""

import ipaddress
import re
import xml.etree.ElementTree as ET

from . import archive, ntp_context
from .core import Entry, MODE_REPLACE, scrub, validate_word
from .debuglog import protect

SHOW = 'show configuration system ntp | display inheritance | display xml groups'
INTERFACES = 'show interfaces terse | display xml'
CLI_CONTEXT = re.compile(
    r'(?:(?:\{(?:master|backup|primary|secondary|line)(?::[\w.-]+)?\}'
    r'|\[edit(?:[ \t]+[^\]\r\n]*)?\])[ \t]*)+'
)
CLI_ROLLBACK_NOTICE = re.compile(
    r'#[ \t]*commit confirmed will be rolled back in \d+ (?:minutes?|seconds?)'
)


def xml(output):
    if '<!DOCTYPE' in output.upper() or '<!ENTITY' in output.upper():
        raise ValueError('Unexpected Junos XML declaration')
    # Netmiko strips the prompt and at most one Junos context line. Separate
    # RE/edit context lines and confirmed-commit notices can remain outside
    # the XML document. Only remove these known lines at its boundaries;
    # never salvage a truncated document or discard arbitrary CLI warnings.
    lines = output.strip().splitlines()
    start, end = 0, len(lines)

    def context(line):
        line = line.strip()
        return not line or CLI_CONTEXT.fullmatch(line) or CLI_ROLLBACK_NOTICE.fullmatch(line)

    while start < end and context(lines[start]):
        start += 1
    while end > start and context(lines[end - 1]):
        end -= 1
    try:
        root = ET.fromstring('\n'.join(lines[start:end]))
    except ET.ParseError as exc:
        # ElementTree's diagnostic contains only the parser reason and position,
        # not configuration text that could include authentication material.
        raise ValueError(f'Junos returned invalid XML: {exc}') from None
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


def plan(before, variables, mode, *, verification=False, notes=None):
    current = [Entry(key=address, line=address, data={'kind': 'server', 'host': address, **row})
               for address, row in before['servers'].items()]
    keys = ntp_context.resolve(current, list(variables['entries']), variables,
                               platform='juniper_junos', notes=notes)
    records = [variables['entries'][key] for key in keys if variables['entries'][key]['kind'] == 'server']
    # Junos keys server statements by address, so the same address cannot be
    # installed in several routing instances as it can on IOS/NX-OS.
    if len({row['host'] for row in records}) != len(records):
        raise ValueError('Existing Junos NTP servers use multiple routing instances; select the routing instance for the new addresses')
    wanted = [ipv4(row['host']) for row in records]
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
    for record in records:
        address = ipv4(record['host'])
        current = before['servers'].get(address)
        base = f'system ntp server {address}'
        if current is None:
            commands.append('set ' + base)
            current = dict(key=None, prefer=False, vrf=None)
        server_key = key_id(str(record['key'])) if record.get('key') else None
        for leaf, field, value in (('key', 'key', server_key), ('prefer', 'prefer', address == prefer),
                                   ('routing-instance', 'vrf', record['vrf'])):
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
    error = re.search(r'(?im)^[ \t]*(?:error:|syntax error|unknown command|permission denied)[^\r\n]*', output or '')
    if error:
        raise ValueError('Junos rejected the operation: ' + error.group(0).strip())
    return output or ''


def commit(connection, command):
    """Read through the configuration prompt and check Junos completion output.

    Netmiko's commit(confirm=True) requires a particular rollback warning,
    which Junos does not always print even when the confirmed commit succeeds.
    Its loose prompt match can also stop at the hostname inside commit output.
    NTP does not change the hostname, so wait for the complete current prompt.
    """
    prompt = connection.find_prompt().strip()
    if not prompt.endswith('#') or '\n' in prompt or '\r' in prompt:
        raise ValueError('Junos commit requires a configuration-mode prompt')
    output = checked(connection.send_command(
        command, expect_string=r'(?m)^' + re.escape(prompt) + r'[ \t]*$',
        read_timeout=120, strip_prompt=True, strip_command=True))
    marker = 'configuration check succeeds' if command == 'commit check' else 'commit complete'
    if not re.search(r'(?m)^[ \t]*(?:re\d+:[ \t]*)?' + re.escape(marker) + r'[ \t]*$', output):
        raise ValueError(f'Junos {command} did not report {marker}:\n{output.strip() or "<empty response>"}')
    return output


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
    stage = 'validating desired NTP settings'
    try:
        _, variables = per_device(list(desired), dict(variables), task.host)
        # Validate desired values before opening a connection.
        plan(dict(servers={}, sources={}, keys={}, trusted=[]), dict(variables), mode)
        stage = 'opening the SSH connection'
        connection = task.host.get_connection('netmiko', task.nornir.config)

        def read(command):
            nonlocal stage
            stage = {'show system commit': 'checking pending commits',
                     SHOW: 'reading NTP configuration',
                     INTERFACES: 'reading source interfaces'}[command]
            return checked(connection.send_command(('run ' if locked else '') + command, read_timeout=60))

        if variables.get('source'):
            variables['source_address'] = source_address(variables['source'], read(INTERFACES))
        before = parse(read(SHOW))
        stage = 'planning NTP changes'
        commands = plan(before, variables, mode, notes=payload['notes'])
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
                stage = 'checking remediation prerequisites'
                if not save or not verify:
                    raise ValueError('Junos NTP remediation requires saving and verification')
                if before['protected']:
                    raise ValueError(payload['advisories'][0])
                stage = 'entering private configuration mode'
                checked(connection.config_mode(config_command='configure private'))
                locked = True
                stage = 'checking candidate changes'
                if checked(connection.send_command('show | compare', read_timeout=60)).strip():
                    raise ValueError('Junos has pending candidate changes; no changes were made')
                commits = read('show system commit')
                if re.search(r'rollback in|commit confirmed|pending', commits, re.I):
                    raise ValueError('Junos has a pending commit; resolve it before remediation')
                if parse(read(SHOW)) != before:
                    raise ValueError('Junos NTP changed during planning; retry the job')
                if variables.get('source') and source_address(variables['source'], read(INTERFACES)) != variables['source_address']:
                    raise ValueError('Junos source address changed during planning; retry the job')
                stage = 'archiving the change plan'
                archive.checkpoint(task, payload)
                attempted = changed_candidate = True
                stage = 'sending NTP configuration'
                checked(connection.send_config_set(commands, exit_config_mode=False))
                stage = 'running commit check'
                commit(connection, 'commit check')
                confirmed_attempted = True
                stage = 'running commit confirmed 5'
                commit(connection, 'commit confirmed 5')
                payload.update(applied=True, saved=False, verified=False)
                after = parse(read(SHOW))
                stage = 'verifying NTP configuration'
                payload['config_after'] = after
                remaining = plan(after, variables, mode, verification=True)
                payload['missing_after'] = [scrub(c, secrets) for c in remaining]
                if remaining:
                    raise ValueError('Junos NTP verification failed; the unconfirmed commit will roll back')
                stage = 'confirming the permanent commit'
                commit(connection, 'commit')
                payload.update(saved=True, verified=True)
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        payload['failure_stage'] = stage
        if confirmed_attempted and not payload['saved']:
            payload['advisories'].append('Commit outcome requires review. Do not confirm it manually; an unconfirmed commit rolls back after five minutes.')
        # Some SSH failures (including EOFError and TimeoutError) have no text.
        # Keep their type, operation and traceback without exposing key material.
        detail = str(exc).strip() or 'no error detail returned'
        error = RuntimeError(scrub(f'Junos NTP failed while {stage}: {type(exc).__name__}: {detail}', secrets))
        return Result(host=task.host, result=payload, changed=attempted, failed=True,
                      exception=error.with_traceback(exc.__traceback__))
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
