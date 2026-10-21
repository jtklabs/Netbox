"""ASA NTP uses per-server nameif sources and no named VRF syntax."""
import ipaddress

from .core import Entry, validate_word
from .ntp_discovery import interface_name

SAMPLE = """ntp server 10.10.10.1 source inside
ntp server 192.0.2.9 source inside
interface GigabitEthernet0/1
 nameif inside
"""


def address(value):
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise ValueError('ASA NTP servers must be IPv4 or IPv6 addresses') from None


def parse(output):
    from .features.ntp import parse_ntp
    entries = parse_ntp(output, platform='cisco_asa')
    for entry in entries:
        if entry.data.get('kind') == 'server':
            row = entry.data
            address(row['host'])
            if row.get('vrf') or row.get('source_address') or row.get('iburst') or row.get('extra'):
                raise ValueError('Unsupported ASA NTP server options; cannot safely preserve routing context')
            if row.get('source'):
                validate_word(row['source'], 'ASA nameif')
    physical = None
    for raw in output.splitlines():
        words = raw.split()
        if words[:1] == ['interface'] and len(words) == 2:
            physical = interface_name(words[1])
        elif words[:1] == ['nameif'] and physical:
            if len(words) != 2:
                raise ValueError('Incomplete ASA nameif configuration')
            name = validate_word(words[1], 'ASA nameif')
            entries.append(Entry(key='asa-interface:' + physical, line=f'interface {physical}\n nameif {name}',
                                 data={'kind': 'asa-interface', 'physical': physical, 'nameif': name}))
        elif raw and not raw[0].isspace():
            physical = None
    return entries


def plan(current, desired, mode, context):
    from .features.ntp import plan_ntp
    variables = context['variables']
    if variables.get('vrf') not in (None, '', 'default'):
        raise ValueError('ASA NTP does not support named VRFs; use its source nameif instead')
    variables['vrf'] = None
    records = {key: dict(value) for key, value in variables['entries'].items()}
    for row in records.values():
        if row.get('vrf') not in (None, '', 'default'):
            raise ValueError('ASA NTP does not support named VRFs')
        if row['kind'] == 'server':
            address(row['host'])
        if row['kind'] in ('key', 'trusted-key'):
            if not row['id'].isdigit() or not 1 <= int(row['id']) <= 4294967295:
                raise ValueError('ASA NTP key ID must be between 1 and 4294967295')
        if row['kind'] == 'key':
            if row['type'] not in ('md5', 'sha1', 'sha256', 'sha512', 'cmac') or len(row['material']) > 32:
                raise ValueError('Unsupported ASA NTP authentication algorithm or key longer than 32 characters')
    if not variables.get('_asa_source_resolved'):
        selected = variables.get('source')
        if selected:
            mappings = [entry.data for entry in current if entry.data.get('kind') == 'asa-interface']
            matches = {row['nameif'] for row in mappings if row['nameif'] == selected}
            if not matches:
                try:
                    physical = interface_name(selected).lower()
                except ValueError:
                    physical = None
                matches = {row['nameif'] for row in mappings if row['physical'].lower() == physical}
                if physical and not matches:
                    raise ValueError('Selected ASA NTP source interface has no readable nameif mapping')
            if len(matches) > 1:
                raise ValueError('Selected ASA NTP source interface has ambiguous nameif mappings')
            selected = next(iter(matches), selected)
            validate_word(selected, 'ASA nameif')
            variables['source'] = selected
            for row in records.values():
                if row['kind'] == 'server':
                    row['source'] = selected
        variables['_asa_source_resolved'] = True
    variables['entries'] = records
    return plan_ntp([entry for entry in current if entry.data.get('kind') != 'asa-interface'],
                    desired, mode, {**context, 'asa_servers': True})
