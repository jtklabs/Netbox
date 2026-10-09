from django.core.exceptions import ValidationError
from django.test import TestCase

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.audit_views import AuditScheduleForm
from netbox_discovery.models import UpgradeJob
from netbox_discovery.upgrade_views import ScheduleForm
from .test_upgrades import UpgradeFixture


class ManagementIPTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()
        from netbox_compliance.tests.factories import feature_standards
        feature_standards()
        self.address = self.device.primary_ip4

    def remove_ip(self):
        self.device.primary_ip4 = self.device.primary_ip6 = None
        self.device.save()

    def create_job(self, operation):
        self.device.primary_ip4 = self.address
        self.device.save()
        data = {'operation': operation}
        if operation in ('audit_config', 'remediate'):
            data['profile'] = {'features': ['ntp'], 'mode': 'replace'}
        return self.scheduled(**data)

    def test_explicit_pickers_exclude_and_reject_unaddressed_devices(self):
        for form_class in (AuditScheduleForm, ScheduleForm):
            field = form_class().fields['devices']
            self.assertEqual(list(field.clean([self.device.pk])), [self.device])
            self.assertIn('has_primary_ip', str(field.widget.render('devices', [])))
        self.remove_ip()
        for form_class in (AuditScheduleForm, ScheduleForm):
            with self.subTest(form=form_class.__name__):
                field = form_class().fields['devices']
                self.assertFalse(field.queryset.filter(pk=self.device.pk).exists())
                with self.assertRaises(ValidationError):
                    field.clean([self.device.pk])

    def test_selection_rejects_missing_ip_before_profile_resolution(self):
        self.remove_ip()
        with self.assertRaisesMessage(queue.QueueError, 'no primary management IP'):
            queue.select_devices(self.user, {'id': [self.device.pk]})

    def test_every_operation_refuses_worker_pickup_after_ip_removed(self):
        for operation in ('audit', 'stage', 'upgrade', 'audit_config', 'remediate'):
            with self.subTest(operation=operation):
                job = self.create_job(operation)
                self.remove_ip()
                self.assertEqual(self.take(), [])
                job.refresh_from_db()
                self.assertEqual(job.status, 'failed')
                self.assertIn('no primary management IP', job.message)

    def test_every_operation_rechecks_ip_before_connection_including_retries(self):
        for operation in ('audit', 'stage', 'upgrade', 'audit_config', 'remediate'):
            with self.subTest(operation=operation):
                job = self.create_job(operation)
                job = self.take()[0]
                self.report(job, 'queued')
                self.remove_ip()
                # A duplicate acknowledgment must not authorize a stale address.
                with self.assertRaisesMessage(queue.QueueError, 'no primary management IP'):
                    self.report(job, 'queued')
                job.refresh_from_db()
                self.assertIsNone(job.started_at)
                UpgradeJob.objects.filter(pk=job.pk).update(status='failed')

    def test_mutating_jobs_recheck_ip_again_before_changes(self):
        for operation in ('stage', 'upgrade', 'remediate'):
            with self.subTest(operation=operation):
                job = self.create_job(operation)
                job = self.take()[0]
                self.report(job, 'queued')
                self.remove_ip()
                with self.assertRaisesMessage(queue.QueueError, 'no primary management IP'):
                    self.report(job, 'ready', 2)
                job.refresh_from_db()
                self.assertIsNone(job.started_at)
                UpgradeJob.objects.filter(pk=job.pk).update(status='failed')

    def test_completed_history_remains_reportable_after_ip_removed(self):
        job = self.create_job('audit')
        job = self.take()[0]
        self.report(job, 'queued')
        self.remove_ip()
        self.report(job, 'completed', 2)
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
