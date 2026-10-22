"""PAN-OS intake validation and atomic pair scheduling."""
import ipaddress
import re


def validate_profile(profile):
    from .upgrade_queue import QueueError
    settings = profile.get('panos')
    if (profile.get('md5') != '' or not isinstance(profile.get('sha256'), str)
            or not re.fullmatch(r'[a-fA-F0-9]{64}', profile['sha256'])):
        raise QueueError('PAN-OS profiles require sha256 and an empty legacy md5 field.')
    if not re.fullmatch(r'(?:PanOS_[a-z0-9-]+|Panorama_pc)-\d+\.\d+\.\d+(?:-h\d+)?', profile['image']):
        raise QueueError('PAN-OS requires an upgrade package, not a VHDX/OVA image.')
    allowed = {'ha_mode', 'members', 'panorama', 'port', 'verify_tls', 'minimum_content', 'base_images',
               'probes', 'required_routes', 'required_bgp_peers', 'required_tunnels', 'routing_engine', 'additional_checks'}
    if not isinstance(settings, dict) or set(settings) - allowed:
        raise QueueError('Provide supported PAN-OS topology and validation settings.')
    mode = settings.get('ha_mode')
    if mode not in {'standalone', 'active-passive', 'active-active'}:
        raise QueueError('PAN-OS ha_mode must be explicit.')
    members = settings.get('members')
    if not isinstance(members, list) or len(members) != (1 if mode == 'standalone' else 2):
        raise QueueError('PAN-OS must identify all members in upgrade order.')
    for member in members:
        if not isinstance(member, dict) or set(member) != {'address', 'serial'}:
            raise QueueError('Each PAN-OS member needs address and serial.')
        try:
            ipaddress.ip_address(member['address'])
        except (ValueError, TypeError):
            raise QueueError('PAN-OS member address must be a literal IP.') from None
        if not isinstance(member['serial'], str) or not re.fullmatch(r'[A-Za-z0-9]{6,32}', member['serial']) or member['serial'].lower() == 'unknown':
            raise QueueError('PAN-OS requires registered device serials.')
    if len({m['address'] for m in members}) != len(members) or len({m['serial'] for m in members}) != len(members):
        raise QueueError('PAN-OS members must have distinct addresses and serials.')


def order_pairs(rows, operation):
    from .models import UpgradeGroup
    from .upgrade_queue import QueueError
    by_address = {row['address']: row for row in rows}
    for row in rows:
        profile = row['profile']
        settings = profile.get('panos') or {}
        if not settings:
            continue
        members = settings['members']
        if row['address'] not in {m['address'] for m in members}:
            raise QueueError('PAN-OS job address is outside the approved topology.')
        if operation != 'upgrade' or settings['ha_mode'] == 'standalone':
            continue
        if any(m['address'] not in by_address for m in members):
            raise QueueError('Schedule both PAN-OS HA members together; the second job waits for the first.')
        first, second = [by_address[m['address']] for m in members]
        if first['profile'] != second['profile'] or first['poller_name'] != second['poller_name']:
            raise QueueError('PAN-OS HA members need the same approved profile and poller.')
        ids = {first['device'].pk, second['device'].pk}
        groups = UpgradeGroup.objects.filter(name__in=set(first['groups']) & set(second['groups']), max_concurrent=1)
        if not any(set(group.members.values_list('pk', flat=True)) == ids for group in groups):
            raise QueueError('PAN-OS HA members require a dedicated two-device upgrade group with concurrency one.')
        second['waits_for'] = sorted(set(second['waits_for']) | {first['device'].pk})
