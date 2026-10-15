"""Standards dispatch skips ineligible devices while retaining request validation."""
from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from dcim.models import Device, VirtualChassis
from netbox_discovery import upgrade_queue as queue, audit_scheduling
from netbox_discovery.models import AuditSchedule, UpgradeJob
from .test_platform_profiles import PlatformProfileTest


class SkipIneligibleTest(TestCase):
    def setUp(self):
        PlatformProfileTest.setUp(self)

    def test_missing_profile_does_not_block_other_device(self):
        Device.objects.filter(pk=self.devices[1].pk).update(platform=None)
        for operation in ('audit_config', 'remediate'):
            skipped = []
            jobs = queue.schedule(self.user, {**self.data, 'operation': operation}, skipped=skipped)
            self.assertEqual([job.device_id for job in jobs], [self.devices[0].pk])
            self.assertEqual([row['id'] for row in skipped], [self.devices[1].pk])
            self.assertIn('profile', skipped[0]['reason'])

    def test_missing_master_and_address_are_reported_without_blocking_valid_device(self):
        broken = VirtualChassis.objects.create(name='No master')
        member = Device.objects.create(name='orphan', site=self.site, role=self.devices[0].role,
                                       device_type=self.devices[0].device_type, virtual_chassis=broken, vc_position=1)
        Device.objects.filter(pk=self.devices[1].pk).update(primary_ip4=None)
        skipped = []
        jobs = queue.schedule(self.user, self.data, skipped=skipped)
        self.assertEqual([job.device_id for job in jobs], [self.devices[0].pk])
        self.assertEqual({row['id'] for row in skipped}, {member.pk,self.devices[1].pk})
        self.assertIn('no master', skipped[0]['reason'])

    def test_other_device_eligibility_errors_do_not_block_batch(self):
        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        from netbox_compliance.definitions import snapshot_for_device
        def snapshot(user, device, *args, **kwargs):
            if device.pk == self.devices[1].pk:
                raise ValidationError('No applicable standard for this device.')
            return snapshot_for_device(user, device, *args, **kwargs)
        with patch('netbox_compliance.definitions.snapshot_for_device', side_effect=snapshot):
            skipped = []
            self.assertEqual(len(queue.schedule(self.user, self.data, skipped=skipped)), 1)
        self.assertEqual(skipped[0]['id'], self.devices[1].pk)

    def test_all_ineligible_and_invalid_explicit_profile_create_nothing(self):
        Device.objects.all().update(platform=None)
        with self.assertRaises(queue.NoEligibleDevices):
            queue.schedule(self.user, self.data)
        with self.assertRaisesRegex(queue.QueueError, 'accessible saved'):
            queue.schedule(self.user, {**self.data,'profile_source':'saved','saved_profile':999999})
        self.assertFalse(UpgradeJob.objects.exists())

    def test_api_preview_and_creation_report_same_skipped_devices(self):
        Device.objects.filter(pk=self.devices[1].pk).update(platform=None)
        data = {k:v for k,v in self.data.items() if k != 'profile'}
        url = reverse('plugins-api:netbox_discovery-api:upgradejob-schedule')
        preview = self.api.post(url, {**data,'preview':True}, format='json')
        self.assertEqual(preview.status_code,200,preview.data)
        self.assertFalse(UpgradeJob.objects.exists())
        created = self.api.post(url, {**data,'preview':False}, format='json')
        self.assertEqual(created.status_code,201,created.data)
        self.assertEqual(len(created.data['jobs']),1)
        self.assertEqual(preview.data['skipped'],created.data['skipped'])

    def test_recurring_dispatch_skips_masterless_chassis(self):
        broken = VirtualChassis.objects.create(name='No master')
        Device.objects.filter(pk=self.devices[1].pk).update(virtual_chassis=broken,vc_position=1)
        schedule = AuditSchedule.objects.create(name='Partial scope',run_as=self.user,
            filters={'site_id':[self.site.pk]},profile_source='model',profile={},next_run_at=timezone.now()-timedelta(minutes=1))
        AuditSchedule.objects.filter(pk=schedule.pk).update(enabled=True,next_run_at=timezone.now()-timedelta(minutes=1))
        run = audit_scheduling.dispatch(schedule.pk)
        self.assertEqual((run.outcome,run.job_count),('queued',1),run.message)
        self.assertIn('no master',run.message)
