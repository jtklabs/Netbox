"""The global ClearPass cluster, scoped by device role tags rather than standards."""
from dcim.models import Device

ROLE_TAGS = {'clearpass-publisher': 'publisher', 'clearpass-subscriber': 'subscriber'}


def snapshot(user, target, *, apply=False):
    from .upgrade_queue import QueueError

    devices = list(Device.objects.filter(tags__slug__in=ROLE_TAGS).distinct().select_related(
        'primary_ip4', 'platform').prefetch_related('tags'))
    ids = {device.pk for device in devices}
    if set(Device.objects.restrict(user, 'view').filter(pk__in=ids).values_list('pk', flat=True)) != ids:
        raise QueueError('You must be able to view every tagged ClearPass cluster member.')
    if apply and set(Device.objects.restrict(user, 'change').filter(pk__in=ids).values_list('pk', flat=True)) != ids:
        raise QueueError('Cluster-wide changes require change permission on every tagged ClearPass device.')
    members = []
    for device in devices:
        tags = {tag.slug for tag in device.tags.all()}
        roles = [role for tag, role in ROLE_TAGS.items() if tag in tags]
        if len(roles) != 1:
            raise QueueError('Each ClearPass device must have exactly one publisher/subscriber role tag.')
        if not device.platform or device.platform.slug != 'aruba-clearpass':
            raise QueueError('ClearPass role tags may only be assigned to ClearPass devices.')
        if device.status != 'active' or not device.primary_ip4:
            raise QueueError('Every tagged ClearPass cluster member must be active with a primary IPv4 address.')
        members.append({'device_id': device.pk, 'address': str(device.primary_ip4.address.ip), 'role': roles[0]})
    if sum(m['role'] == 'publisher' for m in members) != 1:
        raise QueueError('Tag exactly one device clearpass-publisher and all other members clearpass-subscriber.')
    if len({m['address'] for m in members}) != len(members):
        raise QueueError('ClearPass cluster members must have unique management IPv4 addresses.')
    selected = next((m for m in members if m['device_id'] == target.pk), None)
    if not selected:
        raise QueueError('The target must have a ClearPass publisher or subscriber role tag.')
    if apply and selected['role'] != 'publisher':
        raise QueueError('Schedule ClearPass remediation against the tagged publisher only; audit subscribers separately.')
    return sorted(members, key=lambda member: member['device_id'])
