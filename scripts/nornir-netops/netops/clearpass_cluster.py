"""Reusable NetBox role-tag discovery for the single global ClearPass cluster."""
import ipaddress

ROLE_TAGS = {'clearpass-publisher': 'publisher', 'clearpass-subscriber': 'subscriber'}


def discover(client):
    from .netbox import platform_of

    devices = {}
    for tag in ROLE_TAGS:
        # Do not inherit target, site, tenant, active-status or poller filters.
        for device in client.get('dcim/devices/', {'tag': tag}):
            tags = {item.get('slug') for item in device.get('tags', [])}
            if tag not in tags:
                raise ValueError('NetBox did not honor the ClearPass role-tag filter')
            if device['id'] in devices:
                raise ValueError('A ClearPass device appeared in both role-tag queries; check tags and retry')
            devices[device['id']] = device
    members = []
    for device in devices.values():
        roles = [role for tag, role in ROLE_TAGS.items() if tag in {t.get('slug') for t in device.get('tags', [])}]
        if len(roles) != 1:
            raise ValueError('A ClearPass device must have exactly one publisher/subscriber role tag')
        if platform_of(device) != 'aruba_clearpass':
            raise ValueError('ClearPass role tags may only be assigned to ClearPass devices')
        status = device.get('status')
        if (status.get('value') if isinstance(status, dict) else status) != 'active':
            raise ValueError('Every tagged ClearPass cluster member must be active')
        try:
            address = str(ipaddress.IPv4Interface((device.get('primary_ip4') or {})['address']).ip)
        except (KeyError, ValueError, TypeError):
            raise ValueError('Every tagged ClearPass cluster member needs a primary IPv4 address') from None
        members.append({'device_id': device['id'], 'address': address, 'role': roles[0]})
    return validate(members)


def validate(members):
    if not isinstance(members, list) or not members:
        raise ValueError('Tag one ClearPass publisher and all subscribers in NetBox')
    for member in members:
        if (not isinstance(member, dict) or set(member) != {'device_id', 'address', 'role'} or
                type(member['device_id']) is not int or member['device_id'] < 1 or
                member['role'] not in ('publisher', 'subscriber') or not isinstance(member['address'], str)):
            raise ValueError('Invalid ClearPass cluster member snapshot')
        ipaddress.IPv4Address(member['address'])
    if sum(m['role'] == 'publisher' for m in members) != 1:
        raise ValueError('The global ClearPass cluster must have exactly one tagged publisher')
    if len({m['address'] for m in members}) != len(members) or len({m['device_id'] for m in members}) != len(members):
        raise ValueError('ClearPass cluster members must have unique device IDs and management IPv4 addresses')
    return sorted(members, key=lambda member: member['device_id'])
