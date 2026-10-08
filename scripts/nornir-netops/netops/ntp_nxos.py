"""NX-OS NTP: global source-interface and per-server use-vrf syntax."""
from dataclasses import replace

from .core import Entry, validate_word
from .ntp_discovery import global_sources

SAMPLE = '''ntp source-interface mgmt0
ntp server 192.0.2.1 prefer use-vrf management
ntp server 192.0.2.2 use-vrf management
'''


def parse(output):
    from .features.ntp import parse_ntp
    lines = []
    for raw in output.splitlines():
        tokens = raw.strip().split()
        if tokens[:2] == ['ntp', 'server'] and 'use-vrf' in tokens:
            index = tokens.index('use-vrf')
            if index + 1 >= len(tokens):
                raise ValueError('Incomplete NX-OS NTP use-vrf setting')
            vrf = validate_word(tokens[index + 1], 'NTP VRF')
            tokens = tokens[:index] + tokens[index + 2:]
            if vrf != 'default':
                tokens = tokens[:2] + ['vrf', vrf] + tokens[2:]
        lines.append(' '.join(tokens))
    entries = parse_ntp('\n'.join(lines))
    originals = [line.strip() for line in output.splitlines() if line.split()[:2] == ['ntp', 'server']]
    servers = [entry for entry in entries if entry.data.get('kind') == 'server']
    if len(originals) != len(servers):
        raise ValueError('Incomplete NX-OS NTP server configuration')
    lines_by_key = {id(entry): line for entry, line in zip(servers, originals)}
    entries = [replace(entry, line=lines_by_key[id(entry)]) if id(entry) in lines_by_key else entry for entry in entries]
    source = global_sources(output).get('*')
    if source:
        entries.append(Entry(key='source:' + source.lower(), line='ntp source-interface ' + source,
                             data={'kind': 'source', 'source': source}))
    return entries


def plan(current, desired, mode, context):
    from .features.ntp import _server_key, plan_ntp
    variables = context['variables']
    for record in variables['entries'].values():
        if record.get('kind') == 'key':
            if not record['id'].isdigit() or not 1 <= int(record['id']) <= 65535:
                raise ValueError('NX-OS NTP key ID must be between 1 and 65535')
            if record['type'] not in ('md5', 'aes128cmac'):
                raise ValueError('NX-OS NTP supports md5 or aes128cmac authentication')
    # Source is a global setting, not a server option on NX-OS. Compare it
    # independently so moving the source does not reset every server.
    normalized, mapping, records = [], {}, {}
    for key in desired:
        record = variables['entries'][key]
        plain = _server_key(record['host'], record.get('key')) if record['kind'] == 'server' else key
        normalized.append(plain)
        mapping[plain] = key
        records[plain] = {**record, 'source': None} if record['kind'] == 'server' else record
    server_current = [Entry(key=_server_key(e.data['host'], e.data.get('key')), line=e.line,
                            data={**e.data, 'source': None, 'inherited_source': None})
                      if e.data.get('kind') == 'server' else e
                      for e in current if e.data.get('kind') != 'source']
    options = {**variables, 'entries': records, 'server_options': {}}
    add, remove = plan_ntp(server_current, normalized, mode, {**context, 'variables': options})
    variables['server_options'] = {mapping[k]: v for k, v in options['server_options'].items()}
    add = [mapping[key] for key in add]
    source = variables.get('source')
    if source and not any(e.data.get('kind') == 'source' and e.data['source'].lower() == source.lower() for e in current):
        key = 'source:' + source.lower()
        variables['entries'] = {**variables['entries'], key: {'kind': 'source', 'source': source}}
        add.insert(0, key)
    return add, remove
