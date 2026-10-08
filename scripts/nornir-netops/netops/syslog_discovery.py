"""Observe configured syslog sources over read-only SSH and bootstrap NetBox."""
from datetime import datetime, timezone

from . import ntp_discovery
from .core import canonical_platform, validate_word

PLATFORMS = ntp_discovery.PLATFORMS
SOURCE_TAG = ntp_discovery.SOURCE_TAG
SHOW_COMMAND = 'show running-config | include ^logging'
FIELDS = {
    'syslog_discovery': ('json', 'Syslog source discovery', 'Latest SSH observation; not an approved standard.'),
    'syslog_vrf': ('text', 'Syslog VRF', 'Selected syslog VRF. Use default for global routing; blank means unset.'),
}


def source_config(output, platform, operational=None):
    """Require one explicit interface/VRF shared by all remote collectors."""
    platform = canonical_platform(platform)
    if platform not in PLATFORMS:
        raise ValueError(f'Syslog source discovery is not supported for {platform}')
    sources, servers = {}, []
    operational = operational or {}
    for raw in output.splitlines():
        tokens = raw.strip().split()
        if tokens[:1] != ['logging'] or len(tokens) < 2:
            continue
        if tokens[1] not in ('host', 'server', 'vrf', 'source-interface', 'local-interface'):
            continue
        vrf = None
        for keyword in ('vrf', 'use-vrf'):
            if keyword in tokens:
                index = tokens.index(keyword)
                if index + 1 >= len(tokens):
                    raise ValueError('Incomplete syslog VRF configuration')
                vrf = validate_word(tokens[index + 1], 'syslog VRF')
                tokens = tokens[:index] + tokens[index + 2:]
        if len(tokens) < 3:
            raise ValueError('Incomplete syslog source or collector configuration')
        if tokens[1] in ('source-interface', 'local-interface'):
            source = ntp_discovery.interface_name(''.join(tokens[2:]))
            # NX-OS's source setting is global; IOS/EOS sources are VRF-scoped.
            scope = vrf or ('*' if platform == 'cisco_nxos' else 'default')
            if scope in sources and sources[scope] != source:
                raise ValueError('Conflicting syslog source interfaces')
            sources[scope] = source
        elif tokens[1] in ('host', 'server'):
            if tokens[2] == 'ipv6' or ':' in tokens[2]:
                raise ValueError('IPv6 syslog source discovery is not supported')
            # NX-OS defaults vary by release; use status rather than guessing.
            actual = operational.get(tokens[2].lower(), {}).get('vrf')
            if platform == 'cisco_nxos' and actual:
                if vrf and vrf != actual:
                    raise ValueError('Syslog configured and operational VRF disagree')
                vrf = validate_word(actual, 'syslog VRF')
            servers.append({'server': tokens[2], 'vrf': vrf or (
                None if platform == 'cisco_nxos' else 'default')})
    if not servers:
        return {'status': 'unconfigured', 'reason': 'No syslog collectors configured', 'servers': []}
    for server in servers:
        server['source'] = sources.get(server['vrf']) or sources.get('*')
    pairs = {(server['source'], server['vrf']) for server in servers}
    if len(pairs) != 1 or any(source is None or vrf is None for source, vrf in pairs):
        return {'status': 'ambiguous', 'reason': 'Syslog collectors do not share one explicit source interface and VRF',
                'servers': servers}
    source, vrf = pairs.pop()
    return {'status': 'resolved', 'source': source, 'vrf': vrf, 'servers': servers}


def observe(platform, read):
    platform = canonical_platform(platform)
    if platform not in PLATFORMS:
        raise ValueError(f'Syslog source discovery is not supported for {platform}')
    output = read(SHOW_COMMAND)
    operational = None
    if platform == 'cisco_nxos':
        from .syslog_nxos import operational_servers, STATUS_COMMAND
        operational = operational_servers(read(STATUS_COMMAND))
    observation = source_config(output, platform, operational)
    if observation['status'] == 'resolved':
        vrf = ntp_discovery.interface_vrf(
            read('show running-config interface ' + observation['source']), observation['source'])
        observation['interface_vrf'] = vrf
        if vrf != observation['vrf']:
            observation.update(status='ambiguous', reason='Syslog collector VRF and source interface VRF disagree')
    observation.update(platform=platform, checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    return observation


def ensure_fields(client):
    ntp_discovery.ensure_fields(client, FIELDS)


def sync_device(client, host, observation, source_tag=SOURCE_TAG):
    return ntp_discovery.sync_device(client, host, observation, source_tag, feature='syslog')
