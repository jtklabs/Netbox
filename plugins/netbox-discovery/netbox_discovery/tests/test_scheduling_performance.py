"""Submission must stay independent of fleet size; shared inputs load once."""
from unittest.mock import patch

from dcim.models import Device, Platform
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from ipam.models import IPAddress

from netbox_discovery import audit_scheduling, upgrade_queue as queue
from netbox_discovery.models import AuditSchedule, UpgradeJob


class SchedulingPerformanceTest(TestCase):
    def setUp(self):
        from .test_remediation import RemediationTest
        RemediationTest.setUp(self)
        self.client.force_login(self.user)

    def test_200_device_submission_only_saves_schedule(self):
        platform = Platform.objects.create(name='Audit performance', slug='audit-performance')
        base = self.devices[0]
        Device.objects.filter(pk__in=[device.pk for device in self.devices]).update(platform=platform)
        addresses = IPAddress.objects.bulk_create([
            IPAddress(address=f'198.51.100.{i}/24') for i in range(2, 200)])
        Device.objects.bulk_create([
            Device(name=f'perf-{i}', site=self.site, role=base.role, device_type=base.device_type,
                   platform=platform, primary_ip4=address)
            for i, address in enumerate(addresses, start=2)])
        with patch.object(queue, 'schedule', wraps=queue.schedule) as dispatch:
            with CaptureQueriesContext(connection) as queries, self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse('plugins:netbox_discovery:auditschedule_add'), {
                    'name': 'Large audit', 'enabled': True, 'frequency': 'now', 'window_hours': 1,
                    'platforms': [platform.pk], 'profile_source': 'custom',
                    'features': ['ntp'], 'comparison': 'replace'})
            self.assertEqual(response.status_code, 302)
            dispatch.assert_not_called()
        self.assertLess(len(queries), 150)
        self.assertFalse(UpgradeJob.objects.exists())
        schedule = AuditSchedule.objects.get(name='Large audit')
        self.assertContains(self.client.get(schedule.get_absolute_url()), 'Waiting for background dispatch')
        self.assertEqual(audit_scheduling.run_due(), 1)
        run = schedule.runs.get()
        self.assertEqual(run.job_count, 200, run.message)
        self.assertEqual(run.jobs.count(), 200)
        self.assertEqual(audit_scheduling.run_due(), 0)

    def test_prepare_query_count_does_not_grow_per_device(self):
        # Warm permission/content-type caches before comparing query counts.
        queue.prepare(self.user, self.data)
        with CaptureQueriesContext(connection) as single:
            queue.prepare(self.user, {**self.data, 'filters': {'id': [self.devices[0].pk]}})
        with CaptureQueriesContext(connection) as batch:
            queue.prepare(self.user, {**self.data, 'filters': {'id': [device.pk for device in self.devices]}})
        self.assertEqual(len(batch), len(single))

    def test_disabled_or_rolled_back_submission_does_not_dispatch(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            AuditSchedule.objects.create(name='Rollback', frequency='now', run_as=self.user,
                filters={'id': [self.devices[0].pk]}, profile={'features': ['ntp'], 'mode': 'replace'})
            raise RuntimeError('rollback')
        self.assertEqual(audit_scheduling.run_due(), 0)
        schedule = AuditSchedule.objects.create(name='Disabled', frequency='now', run_as=self.user,
            filters={'id': [self.devices[0].pk]}, profile={'features': ['ntp'], 'mode': 'replace'})
        schedule.enabled = False
        schedule.save()
        self.assertEqual(audit_scheduling.run_due(), 0)
        self.assertFalse(UpgradeJob.objects.exists())
