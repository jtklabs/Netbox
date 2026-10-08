"""Illustrative results only, for the isolated profile-review lab. No device I/O."""
from datetime import timedelta

from dcim.models import Device
from django.utils import timezone
from netbox_compliance.models import ConfigCompliance, ConfigStandard

standard = ConfigStandard.objects.get(name='Lab NTP', sites__slug='profile-review-lab')
oldest = standard.revisions.order_by('number').first()
current = standard.revisions.first()
for name, revision, result in (
    ('lab-cisco-1', oldest, 'compliant'),
    ('lab-cisco-2', current, 'compliant'),
    ('lab-arista-1', current, 'non-compliant'),
):
    device = Device.objects.get(name=name, site__slug='profile-review-lab')
    ConfigCompliance.objects.get_or_create(device=device, standard=standard, defaults={
        'standard_revision': revision, 'result': result, 'source': 'manual',
        'last_checked': timezone.now() - timedelta(hours=1),
        'description': 'Illustrative dummy result; no device was contacted.',
        'findings': {'missing': ['192.0.2.102']} if result == 'non-compliant' else {},
    })
print('Illustrative NTP results created for the isolated lab only.')
