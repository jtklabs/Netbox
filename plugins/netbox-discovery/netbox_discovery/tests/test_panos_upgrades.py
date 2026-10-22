"""Pair scheduling and recovery reservations use the real NetBox queue."""
import copy
from django.test import TestCase
from dcim.models import Device
from ipam.models import IPAddress
from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import UpgradeGroup, UpgradeJob
from .test_upgrades import UpgradeFixture


class PaloQueueTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()
        self.peer = Device.objects.create(name='pan-peer', site=self.site, role=self.role,
            device_type=self.device.device_type, primary_ip4=IPAddress.objects.create(address='192.0.2.5/24'))
        self.profile = dict(name='palo-ha-lab', models=['PA-VM'], starting_versions=['12.1.6'], target_version='12.2.3',
            image='PanOS_vm-12.2.3', md5='', sha256='a'*64, minimum_free_bytes=1500000000,
            image_source='/images/PanOS_vm-12.2.3', panos={'ha_mode': 'active-passive',
                'members': [{'address': '192.0.2.5', 'serial': 'SERIAL0002'}, {'address': '192.0.2.4', 'serial': 'SERIAL0001'}],
                'minimum_content': '9010-9583', 'probes': []})
        self.group = UpgradeGroup.objects.create(name='palo-ha', max_concurrent=1)
        self.group.members.set([self.device, self.peer])

    def jobs(self, **kwargs):
        return queue.schedule(self.user, {**self.data, 'profile': self.profile, **kwargs})

    def test_profile_order_overrides_device_primary_key(self):
        self.jobs()
        claimed = self.take()
        self.assertEqual([j.device_id for j in claimed], [self.peer.pk])
        self.assertEqual(self.take(), [])
        self.report(claimed[0], 'ready')
        self.report(claimed[0], 'completed', 2)
        self.assertEqual([j.device_id for j in self.take()], [self.device.pk])

    def test_recovery_reserves_both_members(self):
        self.jobs()
        job = self.take()[0]
        self.report(job, 'ready')
        self.report(job, 'recovery_required', 2)
        self.assertEqual(self.take(), [])
        self.assertEqual(UpgradeJob.objects.get(pk=job.pk).status, 'recovery_required')

    def test_pair_requires_both_selected(self):
        with self.assertRaisesMessage(queue.QueueError, 'both PAN-OS'):
            self.jobs(filters={'id': [self.device.pk]})
        self.assertFalse(UpgradeJob.objects.exists())

    def test_pair_requires_concurrency_one(self):
        self.group.max_concurrent = 2
        self.group.save()
        with self.assertRaisesMessage(queue.QueueError, 'concurrency one'):
            self.jobs()

    def test_unknown_serial_rejected(self):
        self.profile['panos']['members'][0]['serial'] = 'unknown'
        with self.assertRaisesMessage(queue.QueueError, 'registered'):
            self.jobs()

    def test_vhdx_is_not_a_software_upgrade(self):
        self.profile['image'] = 'PanOS_vm-12.2.3.vhdx'
        with self.assertRaisesMessage(queue.QueueError, 'upgrade package'):
            self.jobs()

    def test_audit_can_select_one_member_without_scheduling_its_peer(self):
        jobs = self.jobs(operation='audit', filters={'id': [self.peer.pk]})
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].device_id, self.peer.pk)

    def test_active_active_uses_same_pair_reservation(self):
        self.profile['panos']['ha_mode'] = 'active-active'
        self.jobs()
        self.assertEqual([j.device_id for j in self.take()], [self.peer.pk])
