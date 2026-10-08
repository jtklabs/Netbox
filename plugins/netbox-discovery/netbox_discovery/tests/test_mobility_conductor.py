from dcim.models import Device, Platform, Site
from django.test import TestCase
from extras.models import Tag
from ipam.models import IPAddress

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.mobility_conductor import snapshot
from . import test_remediation


class MobilityConductorTest(TestCase):
    def setUp(self):
        test_remediation.RemediationTest.setUp(self)
        platform = Platform.objects.create(name='ArubaOS', slug='arubaos')
        for device in self.devices:
            device.platform = platform
            device.save()
        self.tag = Tag.objects.create(name='mobility-conductor', slug='mobility-conductor')
        self.conductor = Device.objects.create(
            name='Conductor', site=Site.objects.create(name='Other site', slug='other'),
            role=self.devices[0].role, device_type=self.devices[0].device_type,
            primary_ip4=IPAddress.objects.create(address='192.0.2.254/24'))
        self.conductor.tags.add(self.tag)
        self.data.update(profile={'features': ['ntp'], 'mode': 'add'})

    def test_audits_keep_all_selected_wlcs_and_pin_global_endpoint(self):
        jobs = queue.schedule(self.user, {**self.data, 'operation': 'audit_config'})
        expected = {'device_id': self.conductor.pk, 'address': '192.0.2.254'}
        self.assertEqual({job.device_id for job in jobs}, {device.pk for device in self.devices})
        self.assertTrue(all(job.standards_snapshot['mobility_conductor'] == expected for job in jobs))

    def test_missing_or_multiple_tags_fail_closed(self):
        self.devices[0].tags.add(self.tag)
        with self.assertRaisesRegex(queue.QueueError, 'exactly one'):
            queue.schedule(self.user, self.data)
        self.devices[0].tags.remove(self.tag)
        self.conductor.tags.remove(self.tag)
        with self.assertRaisesRegex(queue.QueueError, 'exactly one'):
            queue.schedule(self.user, self.data)

    def test_inactive_missing_ip_and_self_target_fail(self):
        self.conductor.status = 'offline'
        self.conductor.save()
        with self.assertRaisesRegex(queue.QueueError, 'active'):
            snapshot(self.user, self.devices[0])
        self.conductor.status = 'active'
        self.conductor.primary_ip4 = None
        self.conductor.save()
        with self.assertRaisesRegex(queue.QueueError, 'IPv4'):
            snapshot(self.user, self.devices[0])
        self.conductor.primary_ip4 = IPAddress.objects.create(address=str(self.devices[0].primary_ip4.address))
        self.conductor.save()
        with self.assertRaisesRegex(queue.QueueError, 'managed WLC'):
            snapshot(self.user, self.conductor)
        with self.assertRaises(queue.QueueError):
            snapshot(self.user, self.devices[0])

    def test_ready_gate_checks_acknowledgment_and_conductor_changes(self):
        queue.schedule(self.user, {**self.data, 'filters': {'id': [self.devices[0].pk]}})
        job = queue.claim(self.user, self.poller, 1, True)[0]
        data = {'claim_token': job.claim_token, 'sequence': 1, 'stage': 'ready', 'message': 'Apply',
                'summary': {'standards_revisions': [{'standard_id': p['standard_id'], 'revision': p['revision']}
                                                   for p in job.standards_snapshot['revisions']]}}
        with self.assertRaisesRegex(queue.QueueError, 'acknowledge'):
            queue.report(self.user, job.pk, data)
        data['summary']['mobility_conductor'] = job.standards_snapshot['mobility_conductor']
        ip = self.conductor.primary_ip4
        ip.address = '192.0.2.253/24'
        ip.save()
        with self.assertRaisesRegex(queue.QueueError, 'assignment changed'):
            queue.report(self.user, job.pk, data)
        ip.address = '192.0.2.254/24'
        ip.save()
        self.conductor.tags.remove(self.tag)
        with self.assertRaisesRegex(queue.QueueError, 'exactly one'):
            queue.report(self.user, job.pk, data)
        self.conductor.tags.add(self.tag)
        self.assertEqual(queue.report(self.user, job.pk, data).status, 'running')

    def test_conductor_must_be_visible(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user('no-conductor-view', password='test-only')
        with self.assertRaisesRegex(queue.QueueError, 'view'):
            snapshot(user, self.devices[0])
