"""NX-OS syslog: server-specific severity and operational VRF confirmation."""
import re

from .core import Entry, MODE_REPLACE, normalize, validate_address, validate_word
from .ntp_discovery import interface_name

SHOW_COMMAND = 'show running-config | include ^logging'
STATUS_COMMAND = 'show logging server'
SAMPLE = '''logging server 192.0.2.50 6 use-vrf management
logging source-interface mgmt0
Logging server: enabled
{192.0.2.50}
server severity: informational
server facility: local7
server VRF: management
server port: 514
'''


def operational_servers(output):
    servers, current = {}, None
    for raw in output.splitlines():
        line = raw.strip()
        match = re.fullmatch(r'\{([^{}\s]+)\}', line)
        if match:
            host = normalize(validate_address(match[1]))
            if host in servers:
                raise ValueError('Duplicate NX-OS syslog server status')
            current = servers[host] = {}
        elif current is not None:
            match = re.fullmatch(r'server (VRF|severity|port):\s*(\S+)', line, re.IGNORECASE)
            if match:
                field, value = match[1].lower(), match[2]
                if field in current:
                    raise ValueError('Duplicate NX-OS syslog status field')
                current[field] = value
    return servers


def _key(host, port, vrf, severity):
    return f'host:{normalize(host)}:{port}:vrf:{vrf}:severity:{severity}'


def parse(output):
    from .features.syslog import SEVERITIES
    status = operational_servers(output)
    entries, seen = [], set()
    for raw in output.splitlines():
        line = raw.strip()
        tokens = line.split()
        if tokens[:2] == ['logging', 'source-interface']:
            source = interface_name(''.join(tokens[2:]))
            entries.append(Entry(key='source:' + source, line=line, data={'kind': 'source', 'source': source}))
        elif tokens[:2] == ['logging', 'server']:
            if len(tokens) < 3:
                raise ValueError('Incomplete NX-OS syslog server')
            host = normalize(validate_address(tokens[2]))
            if host in seen:
                raise ValueError('Duplicate NX-OS syslog server configuration')
            seen.add(host)
            rest, options = tokens[3:], {}
            severity = int(rest.pop(0)) if rest and rest[0].isdigit() else None
            while rest:
                option = rest.pop(0)
                if option not in ('port', 'use-vrf', 'facility') or not rest or option in options:
                    raise ValueError('Unsupported NX-OS syslog server options; manual review required')
                options[option] = rest.pop(0)
            actual = status.get(host, {})
            vrf = options.get('use-vrf') or actual.get('vrf')
            if not vrf:
                raise ValueError('NX-OS syslog VRF is missing from configuration and server status')
            vrf = validate_word(vrf, 'syslog VRF')
            if actual.get('vrf') and actual['vrf'] != vrf:
                raise ValueError('NX-OS syslog configuration and operational VRF disagree')
            if severity is None:
                value = actual.get('severity', 'notifications')
                severity = int(value) if value.isdigit() else SEVERITIES.index(value)
            port = int(options.get('port', actual.get('port', '514')))
            if not 0 <= severity <= 7 or not 1 <= port <= 65535:
                raise ValueError('Invalid NX-OS syslog port or severity')
            facility = validate_word(options.get('facility', 'local7'), 'syslog facility')
            entries.append(Entry(key=_key(host, port, vrf, severity), line=line,
                                 data={'kind': 'host', 'host': host, 'port': port, 'vrf': vrf,
                                       'severity': severity, 'facility': facility}))
    return entries


def plan(current, desired, mode, context):
    from .features.syslog import SEVERITIES
    variables = context['variables']
    records = variables['entries']
    severity = next((SEVERITIES.index(records[key]['severity']) for key in desired
                     if records[key]['kind'] == 'trap'), None)
    hosts = [records[key]['host'] for key in desired if records[key]['kind'] == 'host']
    if severity is not None and not hosts:
        raise ValueError('NX-OS syslog severity requires desired collectors')
    if len(set(hosts)) != len(hosts):
        raise ValueError('NX-OS supports only one syslog definition per collector host')
    vrf = variables.get('vrf') or 'default'
    configured = {entry.key for entry in current}
    add = []
    variables['nxos_options'] = {}
    for key in desired:
        record = records[key]
        if record['kind'] == 'host':
            existing = next((entry.data for entry in current if entry.data.get('kind') == 'host'
                             and entry.data['host'] == record['host']), {})
            level = severity if severity is not None else existing.get('severity', 5)
            variables['nxos_options'][key] = {'severity': level, 'facility': existing.get('facility', 'local7')}
            if _key(record['host'], record['port'], vrf, level) not in configured:
                add.append(key)
        elif record['kind'] == 'source':
            if 'source:' + interface_name(record['source']) not in configured:
                add.append(key)
    # A server is keyed by host on NX-OS. Re-setting it updates its options;
    # negating the old line afterwards would delete the updated server too.
    remove = [entry for entry in current if entry.data.get('kind') == 'host'
              and entry.data['host'] not in hosts] if mode == MODE_REPLACE else []
    return add, remove


def reverse(commands, current, removed, context):
    from .rollback import Reversal
    undo = []
    for command in commands:
        if command.startswith('logging server '):
            undo.append('no logging server ' + command.split()[2])
        elif command.startswith('logging source-interface '):
            undo.append('no logging source-interface')
    for entry in current:
        if entry.data['kind'] == 'host':
            row = entry.data
            undo.append(f"logging server {row['host']} {row['severity']} port {row['port']} "
                        f"facility {row['facility']} use-vrf {row['vrf']}")
        else:
            undo.append(entry.line)
    return Reversal(commands=undo)
