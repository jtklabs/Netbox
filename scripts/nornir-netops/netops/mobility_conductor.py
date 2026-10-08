"""Resolve the single global Mobility Conductor from NetBox inventory."""
import ipaddress

TAG = 'mobility-conductor'


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'device_id', 'address'} or
            type(value['device_id']) is not int or value['device_id'] < 1 or
            not isinstance(value['address'], str)):
        raise ValueError('Invalid Mobility Conductor inventory snapshot')
    ipaddress.IPv4Address(value['address'])
    return dict(value)


def discover(client):
    rows = client.get('dcim/devices/', {'tag': TAG})
    if len(rows) != 1:
        raise ValueError('Tag exactly one NetBox device mobility-conductor')
    device = rows[0]
    if TAG not in {tag.get('slug') for tag in device.get('tags', [])}:
        raise ValueError('NetBox did not honor the mobility-conductor tag filter')
    status = device.get('status')
    if (status.get('value') if isinstance(status, dict) else status) != 'active':
        raise ValueError('The tagged Mobility Conductor must be active')
    try:
        address = str(ipaddress.IPv4Interface((device.get('primary_ip4') or {})['address']).ip)
    except (KeyError, ValueError, TypeError):
        raise ValueError('The tagged Mobility Conductor needs a primary management IPv4 address') from None
    return validate({'device_id': device['id'], 'address': address})
