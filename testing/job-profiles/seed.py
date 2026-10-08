"""Run with manage.py shell in an empty, isolated lab. Never attach a device worker."""
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from extras.models import Tag
from ipam.models import IPAddress
from netbox_discovery.models import DeviceTypeProfile, DiscoveryPoller, JobProfile
from netbox_compliance.models import ConfigStandard
from netbox_compliance.revisions import revision_batch


site, _ = Site.objects.get_or_create(name='Profile review lab', slug='profile-review-lab')
tag, _ = Tag.objects.get_or_create(name='poller-profile-lab', slug='poller-profile-lab')
site.tags.add(tag)
for slug in ('clearpass-publisher', 'clearpass-subscriber', 'mobility-conductor'):
    Tag.objects.get_or_create(slug=slug, defaults={'name': slug})
role, _ = DeviceRole.objects.get_or_create(name='Lab access', slug='lab-access')
DiscoveryPoller.objects.get_or_create(name='profile-lab')
fix, _ = JobProfile.objects.get_or_create(name='Lab - time and logging', defaults={
    'kind': 'remediate', 'plan': {'features': ['ntp', 'syslog'], 'mode': 'add'},
    'description': 'Dummy configuration plan for the review lab.'})

for name, definition in (
    ('Lab NTP', 'ntp:\n  servers:\n    - 192.0.2.100\n    - 192.0.2.101\n'),
    ('Lab Syslog', 'syslog:\n  destinations:\n    - 192.0.2.110\n'),
):
    with revision_batch():
        standard, created = ConfigStandard.objects.get_or_create(name=name, defaults={
            'check_type': 'netops', 'definition_yaml': definition,
            'description': 'Dummy standard for the isolated review lab.',
        })
        if created:
            standard.sites.add(site)

for index, (vendor, model, start, target, image) in enumerate((
    ('Cisco', 'C9350-48P', '17.18.1', '17.18.4', 'cisco9k_iosxe.17.18.04.SPA.bin'),
    ('Arista', 'DCS-7050SX3-48YC8', '4.32.1F', '4.32.3M', 'EOS-4.32.3M.swi'),
), start=1):
    manufacturer, _ = Manufacturer.objects.get_or_create(name=vendor, slug=vendor.lower())
    dtype, _ = DeviceType.objects.get_or_create(manufacturer=manufacturer, model=model,
                                               defaults={'slug': model.lower()})
    profile, _ = JobProfile.objects.get_or_create(name=f'Lab - {vendor} upgrade', defaults={
        'kind': 'upgrade', 'description': 'Illustrative only; checksum and release path are dummy data.',
        'plan': {'name': f'lab-{vendor.lower()}', 'models': [model], 'starting_versions': [start],
                 'target_version': target, 'image': image, 'md5': 'a' * 32, 'minimum_free_bytes': 2000000000}})
    DeviceTypeProfile.objects.get_or_create(device_type=dtype, defaults={
        'upgrade_profile': profile, 'remediation_profile': fix})
    for member in range(1, 3):
        ip, _ = IPAddress.objects.get_or_create(address=f'192.0.2.{index * 10 + member}/24')
        Device.objects.get_or_create(name=f'lab-{vendor.lower()}-{member}', defaults={
            'site': site, 'role': role, 'device_type': dtype, 'primary_ip4': ip})

print('Profile review lab: 4 devices, 2 models, 3 saved profiles. No device workers are configured.')
