"""Pin the global Conductor endpoint independently of a job's WLC filters."""
import ipaddress

from dcim.models import Device


def snapshot(user, target):
    from .upgrade_queue import QueueError

    devices = list(Device.objects.filter(tags__slug='mobility-conductor').distinct().select_related('primary_ip4'))
    if len(devices) != 1:
        raise QueueError('Tag exactly one NetBox device mobility-conductor.')
    conductor = devices[0]
    if not Device.objects.restrict(user, 'view').filter(pk=conductor.pk).exists():
        raise QueueError('You must be able to view the tagged Mobility Conductor.')
    if conductor.status != 'active' or not conductor.primary_ip4:
        raise QueueError('The tagged Mobility Conductor must be active with a primary management IPv4 address.')
    if conductor.pk == target.pk or conductor.primary_ip4_id == target.primary_ip4_id:
        raise QueueError('Select managed WLC devices, not the tagged Mobility Conductor.')
    try:
        address = str(ipaddress.IPv4Interface(str(conductor.primary_ip4.address)).ip)
    except ValueError:
        raise QueueError('The tagged Mobility Conductor needs a primary management IPv4 address.') from None
    if target.primary_ip4 and str(ipaddress.ip_interface(str(target.primary_ip4.address)).ip) == address:
        raise QueueError('The WLC and Mobility Conductor must have different management IPv4 addresses.')
    return {'device_id': conductor.pk, 'address': address}
