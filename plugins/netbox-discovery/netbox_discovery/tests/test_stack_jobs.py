from dcim.models import Device, DeviceType, VirtualChassis
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from users.models import ObjectPermission

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import DeviceTypeProfile, JobProfile, UpgradeJob
from .test_upgrades import UpgradeFixture


class StackJobTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()
        self.stack = VirtualChassis.objects.create(name='Stack', master=self.device)
        self.device.virtual_chassis = self.stack
        self.device.vc_position = 1
        self.device.save()
        self.member = Device.objects.create(name='sw2', site=self.site, role=self.role,
                                            device_type=self.device.device_type,
                                            virtual_chassis=self.stack, vc_position=2)

    def test_fleet_has_one_job_per_stack(self):
        jobs = queue.schedule(self.user, self.data)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].device_id, self.device.pk)
        self.assertEqual(jobs[0].address, '192.0.2.4')

    def test_member_only_selection_resolves_master(self):
        job = self.scheduled(filters={'id': [self.member.pk]})
        self.assertEqual(job.device_id, self.device.pk)

    def test_only_master_needs_a_default_profile(self):
        self.member.device_type = DeviceType.objects.create(
            manufacturer=self.device.device_type.manufacturer, model='Member model', slug='member-model')
        self.member.save()
        profile = JobProfile.objects.create(name='Master profile', kind='upgrade', plan=self.data['profile'])
        DeviceTypeProfile.objects.create(device_type=self.device.device_type, upgrade_profile=profile)
        job = self.scheduled(profile=None, profile_source='model')
        self.assertEqual(job.device_id, self.device.pk)
        self.assertEqual(job.profile_name, profile.name)

    def test_master_ip_is_still_required(self):
        self.device.primary_ip4 = None
        self.device.save()
        with self.assertRaisesMessage(queue.QueueError, 'no primary management IP'):
            self.scheduled()
        self.assertFalse(UpgradeJob.objects.exists())

    def test_missing_master_fails_clearly(self):
        self.stack.master = None
        self.stack.save()
        with self.assertRaisesMessage(queue.QueueError, 'has no master'):
            self.scheduled()

    def test_master_permissions_are_not_bypassed(self):
        user = get_user_model().objects.create_user('stack-member-reader')
        permission = ObjectPermission.objects.create(name='Member only', actions=['view'],
                                                     constraints={'pk': self.member.pk})
        permission.object_types.add(ContentType.objects.get_for_model(Device))
        permission.users.add(user)
        with self.assertRaisesMessage(queue.QueueError, 'outside your device permissions'):
            queue.select_devices(user, {'id': [self.member.pk]})
