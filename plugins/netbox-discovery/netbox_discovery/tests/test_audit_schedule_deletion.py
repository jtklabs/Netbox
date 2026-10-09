from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from netbox_compliance.job_results import record_job_results
from netbox_compliance.models import ConfigCompliance
from netbox_discovery import audit_scheduling
from netbox_discovery.models import AuditRun, AuditSchedule, UpgradeJob
from .test_remediation import RemediationTest


class AuditScheduleDeletionTest(TestCase):
    def setUp(self):
        RemediationTest.setUp(self)
        self.schedule = AuditSchedule.objects.create(
            name='Retired daily NTP', run_as=self.user, filters={'site_id': [self.site.pk]},
            profile={'features': ['ntp'], 'mode': 'replace'})
        self.schedule_id = self.schedule.pk
        now = timezone.now()
        AuditSchedule.objects.filter(pk=self.schedule_id).update(next_run_at=now)
        self.run = audit_scheduling.dispatch(self.schedule_id, now)
        self.browser = Client()
        self.browser.force_login(self.user)
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.url = reverse('plugins:netbox_discovery:auditschedule_delete', args=[self.schedule_id])
        self.api_url = reverse('plugins-api:netbox_discovery-api:auditschedule-detail', args=[self.schedule_id])

    def assert_history_retained(self):
        self.assertFalse(AuditSchedule.objects.filter(pk=self.schedule_id).exists())
        self.run.refresh_from_db()
        self.assertIsNone(self.run.schedule_id)
        self.assertEqual(self.run.schedule_name, 'Retired daily NTP')
        self.assertEqual(self.run.jobs.count(), 2)
        self.assertIn('Retired daily NTP (deleted)', str(self.run))
        self.assertIsNone(audit_scheduling.dispatch(self.schedule_id))
        self.assertEqual(audit_scheduling.run_due(), 0)

    def test_browser_delete_preserves_completed_jobs_and_compliance(self):
        self.run.jobs.update(status='completed', completed_at=timezone.now())
        job = self.run.jobs.first()
        record_job_results(job, {'compliance_results': {'ntp': 'non-compliant'}}, timezone.now())
        record = ConfigCompliance.objects.get(device_id=job.device_id)
        result_before = (record.result, record.findings, record.last_checked)
        confirmation = self.browser.get(self.url)
        self.assertContains(confirmation, 'Run history, device jobs, and compliance results will be kept.')
        self.assertContains(confirmation, 'Already-queued or running jobs are not cancelled')
        response = self.browser.post(self.url, {'confirm': True})
        self.assertEqual(response.status_code, 302, response.content)
        self.assert_history_retained()
        record.refresh_from_db()
        self.assertEqual((record.result, record.findings, record.last_checked), result_before)
        self.assertEqual(set(self.run.jobs.values_list('status', flat=True)), {'completed'})
        self.assertContains(self.browser.get(self.run.get_absolute_url()), 'Retired daily NTP (deleted)')
        self.assertContains(self.browser.get(job.get_absolute_url()), 'Retired daily NTP (deleted)')
        self.assertContains(self.browser.get(reverse('plugins:netbox_discovery:auditrun_list'),
                                             {'q': 'Retired daily NTP'}), 'Retired daily NTP (deleted)')

    def test_api_delete_retains_pending_and_running_jobs_and_exposes_history(self):
        UpgradeJob.objects.filter(pk=self.run.jobs.first().pk).update(status='running')
        before = list(self.run.jobs.order_by('pk').values_list('pk', 'status'))
        self.assertEqual(self.api.delete(self.api_url).status_code, 204)
        self.assert_history_retained()
        self.assertEqual(list(self.run.jobs.order_by('pk').values_list('pk', 'status')), before)
        response = self.api.get(reverse('plugins-api:netbox_discovery-api:auditrun-detail', args=[self.run.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()['schedule'])
        self.assertEqual(response.json()['schedule_name'], 'Retired daily NTP')
        self.assertEqual(response.json()['job_status_counts'], {'pending': 1, 'running': 1})

    def test_htmx_delete_confirmation_includes_retention_warning(self):
        response = self.browser.get(self.url, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Already-queued or running jobs are not cancelled')
        self.assertContains(response, 'name="confirm"')

    def test_queryset_deletion_also_retains_history(self):
        AuditSchedule.objects.filter(pk=self.schedule_id).delete()
        self.assert_history_retained()

    def test_original_name_is_kept_when_schedule_is_renamed(self):
        self.schedule.name = 'Renamed daily NTP'
        self.schedule.save()
        self.run.message = 'Updated result'
        self.run.save()
        self.schedule.delete()
        self.assert_history_retained()

    def test_delete_permission_is_still_required(self):
        reader = get_user_model().objects.create_user('no-delete', password='test-only')
        self.api.force_authenticate(reader)
        self.assertEqual(self.api.delete(self.api_url).status_code, 403)
        self.browser.force_login(reader)
        self.assertEqual(self.browser.post(self.url, {'confirm': True}).status_code, 403)
        self.assertTrue(AuditSchedule.objects.filter(pk=self.schedule_id).exists())

    def test_backfill_preserves_existing_schedule_names(self):
        AuditRun.objects.filter(pk=self.run.pk).update(schedule_name='')
        migration = import_module('netbox_discovery.migrations.0022_preserve_runs_after_schedule_deletion')
        with connection.schema_editor() as editor:
            migration.preserve_schedule_names(apps, editor)
        self.schedule.delete()
        self.assert_history_retained()
