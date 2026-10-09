"""Read NTP source configuration over SSH; optionally bootstrap NetBox intent."""
from datetime import datetime, timezone
import re

from . import archive
from .core import canonical_platform, validate_word
from .netbox import NetBoxError, SERVICE_SOURCE_TAG, LEGACY_SOURCE_TAGS

PLATFORMS = ('cisco_ios', 'arista_eos', 'cisco_nxos', 'juniper_junos')
SOURCE_TAG = SERVICE_SOURCE_TAG
FIELDS = {
    'ntp_discovery': ('json', 'NTP source discovery', 'Latest SSH observation; not an approved standard.'),
    'ntp_vrf': ('text', 'NTP VRF', 'Selected NTP VRF. Use default for the global routing table; blank means unset.'),
}
SHOW_COMMANDS = ('show running-config | include ^ntp.server',
                 'show running-config | include ^ntp.source',
                 'show running-config | include ^ntp.local-interface')


def interface_name(value):
    value = re.sub(r'\s+', '', value)
    if re.fullmatch(r'(?:lo\d+|irb|fxp\d+|em\d+|ae\d+|reth\d+|vlan|vme|(?:ge|xe|et)-\d+/\d+/\d+(?::\d+)?)\.\d+', value):
        return value
    if value.lower() == 'mgmt0':
        return 'mgmt0'
    aliases = {'lo': 'Loopback', 'loopback': 'Loopback', 'vl': 'Vlan', 'vlan': 'Vlan',
               'ma': 'Management', 'management': 'Management', 'mgmt': 'Management',
               'et': 'Ethernet', 'ethernet': 'Ethernet', 'gi': 'GigabitEthernet',
               'gigabitethernet': 'GigabitEthernet', 'te': 'TenGigabitEthernet',
               'tengigabitethernet': 'TenGigabitEthernet', 'po': 'Port-channel',
               'port-channel': 'Port-channel'}
    match = re.fullmatch(r'([A-Za-z-]+)([0-9][0-9/.:]*)', value)
    if not match or len(value) > 64:
        raise ValueError('NTP source is not a recognized interface name')
    return aliases.get(match[1].lower(), match[1]) + match[2]


def global_sources(output):
    sources = {}
    for raw in output.splitlines():
        tokens = raw.strip().split()
        if tokens[:2] not in (['ntp', 'source'], ['ntp', 'source-interface'], ['ntp', 'local-interface']):
            continue
        rest = tokens[2:]
        vrf = '*' if tokens[1] in ('source', 'source-interface') else 'default'
        if rest[:1] == ['vrf']:
            if len(rest) < 3:
                raise ValueError('Incomplete NTP source VRF configuration')
            vrf, rest = validate_word(rest[1], 'NTP VRF'), rest[2:]
        source = interface_name(''.join(rest))
        if vrf in sources and sources[vrf] != source:
            raise ValueError('Conflicting global NTP source interfaces')
        sources[vrf] = source
    return sources


def source_config(output, platform):
    """Resolve explicit source settings only; never infer a routed egress port."""
    if platform not in PLATFORMS:
        raise ValueError(f'NTP source discovery is not supported for {platform}')
    globals_, servers = global_sources(output), []
    for raw in output.splitlines():
        tokens = raw.strip().split()
        if tokens[:2] == ['ntp', 'server']:
            rest, vrf = tokens[2:], 'default'
            if rest[:1] == ['vrf']:
                if len(rest) < 3:
                    raise ValueError('Incomplete NTP server VRF configuration')
                vrf, rest = rest[1], rest[2:]
            if not rest:
                raise ValueError('Incomplete NTP server configuration')
            if platform == 'cisco_nxos' and 'use-vrf' in rest:
                index = rest.index('use-vrf')
                if index + 1 >= len(rest):
                    raise ValueError('Incomplete NTP server VRF configuration')
                vrf = rest[index + 1]
            source = None
            if 'source' in rest[1:]:
                index = rest.index('source', 1) + 1
                if index >= len(rest):
                    raise ValueError('Incomplete NTP server source')
                value = rest[index]
                if index + 1 < len(rest) and rest[index + 1].isdigit() and value.isalpha():
                    value += rest[index + 1]
                source = interface_name(value)
            servers.append({'server': rest[0], 'vrf': validate_word(vrf, 'NTP VRF'), 'source': source})
    if not servers:
        return {'status': 'unconfigured', 'reason': 'No NTP servers configured', 'servers': []}
    for server in servers:
        if not server['source']:
            server['source'] = globals_.get(server['vrf'])
            if platform in ('cisco_ios', 'cisco_nxos'):
                server['source'] = server['source'] or globals_.get('*')
    pairs = {(server['source'], server['vrf']) for server in servers}
    if len(pairs) != 1 or any(source is None for source, _ in pairs):
        return {'status': 'ambiguous', 'reason': 'NTP servers do not share one explicit source interface and VRF',
                'servers': servers}
    source, vrf = pairs.pop()
    return {'status': 'resolved', 'source': source, 'vrf': vrf, 'servers': servers}


def interface_vrf(output, expected):
    names, vrfs = [], set()
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith('interface '):
            names.append(interface_name(line.split(' ', 1)[1]))
        match = re.fullmatch(r'(?:ip )?vrf (?:(?:forwarding|member) )?(\S+)', line)
        if match:
            vrfs.add(validate_word(match[1], 'interface VRF'))
    if [name.lower() for name in names] != [expected.lower()] or len(vrfs) > 1:
        raise ValueError('Source interface configuration was missing or ambiguous')
    return next(iter(vrfs), 'default')


def observe(platform, read):
    """Read one source/VRF observation using the caller's existing SSH session."""
    platform = canonical_platform(platform)
    if platform == 'juniper_junos':
        from .junos_ntp import observe as observe_junos
        observation = observe_junos(read)
        observation.update(platform=platform, checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        return observation
    if platform not in PLATFORMS:
        raise ValueError(f'NTP source discovery is not supported for {platform}')
    observation = source_config('\n'.join(read(command) for command in SHOW_COMMANDS), platform)
    if observation['status'] == 'resolved':
        vrf = interface_vrf(read('show running-config interface ' + observation['source']), observation['source'])
        observation['interface_vrf'] = vrf
        if vrf != observation['vrf']:
            observation.update(status='ambiguous', reason='NTP server VRF and source interface VRF disagree')
    observation.update(platform=platform, checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return observation


def discover(task):
    from nornir.core.task import Result
    from .runner import _check_understood, netmiko_send_command, SHOW_TIMEOUT

    def read(command):
        output = task.run(task=netmiko_send_command, name=command, command_string=command,
                          enable=True, read_timeout=SHOW_TIMEOUT).result or ''
        _check_understood(command, output)
        return output

    observation = observe(task.host.platform, read)
    return Result(host=task.host, result=observation, changed=False)


def ensure_fields(client, fields=None):
    missing = []
    for name, (kind, label, description) in (FIELDS if fields is None else fields).items():
        rows = client.get('extras/custom-fields/', {'name': name})
        if not rows:
            missing.append((name, kind, label, description))
            continue
        field = rows[0]
        actual_type = field.get('type')
        actual_type = actual_type.get('value') if isinstance(actual_type, dict) else actual_type
        if len(rows) != 1 or field.get('name') != name or actual_type != kind or 'dcim.device' not in field.get('object_types', []):
            raise NetBoxError(f'{name} must be a {kind} custom field assigned to dcim.device')
    for name, kind, label, description in missing:
        client.request_object('POST', 'extras/custom-fields/', {
            'name': name, 'type': kind, 'label': label, 'description': description,
            'object_types': ['dcim.device'], 'required': False,
            'group_name': label.split(' ', 1)[0], 'is_cloneable': False,
        })


def sync_device(client, host, observation, source_tag=SOURCE_TAG, *, feature='ntp'):
    """Bootstrap only missing intent; later discovery never moves an existing tag."""
    label = feature.upper()
    vrf_field = f'{feature}_vrf'
    device_id = host.data.get('netbox_id')
    if not device_id:
        raise NetBoxError(f'{label} discovery writeback requires NetBox inventory identity')
    path = f'dcim/devices/{int(device_id)}/'
    client.request_object('PATCH', path, {'custom_fields': {f'{feature}_discovery': observation}})
    if observation['status'] != 'resolved':
        return {'status': 'attention', 'reason': observation.get('reason', observation['status'])}
    device = client.request_object('GET', path)
    rows = client.get('dcim/interfaces/', {'device_id': device_id})
    if any((row.get('device') or {}).get('id') != device_id for row in rows):
        raise NetBoxError('Interface response includes another device')
    matches = []
    for row in rows:
        try:
            if interface_name(row['name']).lower() == observation['source'].lower():
                matches.append(row)
        except ValueError:
            continue
    if len(matches) != 1:
        return {'status': 'attention', 'reason': 'Source interface does not match exactly one NetBox interface'}
    interface = matches[0]
    tagged = [row for row in rows if any(tag.get('slug') == source_tag for tag in row.get('tags', []))]
    if source_tag == SERVICE_SOURCE_TAG and not tagged:
        legacy = [row for row in rows if any(tag.get('slug') in LEGACY_SOURCE_TAGS for tag in row.get('tags', []))]
        if legacy and {row['id'] for row in legacy} != {interface['id']}:
            return {'status': 'attention', 'reason': 'Legacy source tags disagree with discovery; select one service-source interface'}
    selected_vrf = (device.get('custom_fields') or {}).get(vrf_field)
    if ((tagged and [row['id'] for row in tagged] != [interface['id']])
            or (selected_vrf and selected_vrf != observation['vrf'])):
        return {'status': 'attention', 'reason': f'Existing {label} selection differs; manual settings preserved'}
    if not tagged:
        tags = client.get('extras/tags/', {'slug': source_tag})
        if not tags:
            tags = [client.request_object('POST', 'extras/tags/', {'name': source_tag, 'slug': source_tag})]
        if len(tags) != 1 or tags[0]['slug'] != source_tag:
            raise NetBoxError(f'{label} source tag lookup was ambiguous')
        interface_path = f"dcim/interfaces/{int(interface['id'])}/"
        current = client.request_object('GET', interface_path)
        client.request_object('PATCH', interface_path, {'tags': list(dict.fromkeys(
            [tag['id'] for tag in current.get('tags', [])] + [tags[0]['id']]))})
        written = client.request_object('GET', interface_path)
        if not any(tag.get('slug') == source_tag for tag in written.get('tags', [])):
            raise NetBoxError(f'{label} interface tag did not verify after write')
    if not selected_vrf:
        current = client.request_object('GET', path)
        current_vrf = (current.get('custom_fields') or {}).get(vrf_field)
        if current_vrf and current_vrf != observation['vrf']:
            raise NetBoxError(f'{label} VRF changed during discovery; manual setting preserved')
        client.request_object('PATCH', path, {'custom_fields': {vrf_field: observation['vrf']}})
    actual = client.request_object('GET', path)
    if (actual.get('custom_fields') or {}).get(vrf_field) != observation['vrf']:
        raise NetBoxError(f'{label} VRF did not verify after write')
    return {'status': 'written' if not tagged or not selected_vrf else 'unchanged',
            'interface_id': interface['id'], 'source': interface['name'], 'vrf': observation['vrf']}


def run(args, style, log):
    from .cli import _connect, _exception_of, PROJECT_ROOT, EXIT_USAGE, EXIT_FAILED, EXIT_DIFF, EXIT_OK
    from .standards import Standards, StandardsError, load
    from .netbox import settings_from
    from .debuglog import redact
    if not args.netbox:
        print('NTP source discovery requires --netbox')
        return EXIT_USAGE
    try:
        args.standards = Standards() if args.no_standards else load(args.standards, PROJECT_ROOT)
        source_tag = settings_from(args.standards, args)['source_tags'].get('ntp')
        if args.sync_netbox and not source_tag:
            raise ValueError('NTP source tag must be enabled for NetBox writeback')
    except (StandardsError, NetBoxError, ValueError) as exc:
        print(style.bad(f'error: {exc}'))
        return EXIT_USAGE
    targets, _, code = _connect(args, style)
    if targets is None:
        return code
    client = args._netbox_client
    if args.sync_netbox:
        ensure_fields(client)
    print(f'inventory: NetBox ({len(targets.inventory.hosts)} device(s))')
    results = targets.run(task=discover)
    records = {}
    for name, result in results.items():
        host = targets.inventory.hosts[name]
        observation = ({'status': 'error', 'reason': redact(str(_exception_of(result))),
                        'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
                       if result.failed else result[0].result)
        record = {'hostname': host.hostname, 'status': observation['status'], 'observation': observation}
        if args.sync_netbox:
            try:
                record['netbox_writeback'] = sync_device(client, host, observation, source_tag)
                if record['netbox_writeback']['status'] == 'attention' and record['status'] != 'error':
                    record['status'] = 'attention'
            except NetBoxError as exc:
                record.update(status='error', error=redact(str(exc)))
        records[name] = record
        detail = (record.get('error') or record.get('netbox_writeback', {}).get('reason')
                  or observation.get('reason') or f"{observation['source']} / VRF {observation['vrf']}")
        print(f'{name}: {record["status"]}: {detail}')
    archive.capture(records)
    if any(record['status'] == 'error' for record in records.values()):
        return EXIT_FAILED
    return EXIT_OK if all(record['status'] == 'resolved' for record in records.values()) else EXIT_DIFF
