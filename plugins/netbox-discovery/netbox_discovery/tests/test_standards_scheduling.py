from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from netbox_discovery import audit_scheduling, upgrade_queue as queue
from netbox_discovery.audit_views import AuditScheduleForm
from netbox_discovery.models import AuditSchedule, AuditRun, UpgradeJob


class StandardsSchedulingTest(TestCase):
    def setUp(self):
        from .test_remediation import RemediationTest
        RemediationTest.setUp(self)
        self.browser = Client()
        self.browser.force_login(self.user)

    def schedule(self, **values):
        return AuditSchedule.objects.create(name='Standards', filters={'site_id': [self.site.pk]},
            profile={'features': ['ntp'], 'mode': 'replace'}, run_as=self.user, **values)

    def test_now_waits_for_worker_and_never_replays_on_edit(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            schedule = self.schedule(frequency='now')
            self.assertFalse(UpgradeJob.objects.exists())
        self.assertEqual(len(callbacks), 0)
        self.assertFalse(UpgradeJob.objects.exists())
        self.assertEqual(audit_scheduling.run_due(), 1)
        self.assertEqual(set(UpgradeJob.objects.values_list('operation', flat=True)), {'audit_config'})
        schedule.refresh_from_db()
        self.assertFalse(schedule.enabled)
        self.assertIsNone(schedule.next_run_at)
        self.assertEqual(schedule.runs.count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            schedule.enabled = True
            schedule.description = 'Edit, not a second run'
            schedule.save()
        self.assertFalse(schedule.enabled)
        audit_scheduling.run_due()
        self.assertEqual(schedule.runs.count(), 1)

    def test_remediation_mode_uses_apply_worker_and_can_be_edited_without_changing_mode(self):
        with self.captureOnCommitCallbacks(execute=True):
            schedule = self.schedule(frequency='now', remediate=True)
        audit_scheduling.run_due()
        job = schedule.runs.get().jobs.first()
        self.assertEqual(job.operation, 'remediate')
        self.assertEqual(queue.claim(self.user, self.poller, 1, False), [])
        edited = queue.edit_pending(self.user, job.pk, {**self.data, 'description': '', 'last_updated': job.last_updated})
        self.assertEqual(edited.operation, 'remediate')
        self.assertEqual(len(queue.claim(self.user, self.poller, 1, True)), 1)

    def test_daily_remediation_keeps_its_next_occurrence(self):
        schedule = self.schedule(remediate=True)
        due = timezone.now() - timedelta(seconds=1)
        AuditSchedule.objects.filter(pk=schedule.pk).update(next_run_at=due)
        run = audit_scheduling.dispatch(schedule.pk)
        self.assertEqual(run.outcome, 'queued', run.message)
        self.assertEqual(set(run.jobs.values_list('operation', flat=True)), {'remediate'})
        schedule.refresh_from_db()
        self.assertTrue(schedule.enabled)
        self.assertGreater(schedule.next_run_at, timezone.now())

    def test_failed_or_empty_now_schedule_is_consumed(self):
        with self.captureOnCommitCallbacks(execute=True):
            schedule = self.schedule(frequency='now', enabled=False)
        self.assertFalse(AuditRun.objects.exists())
        schedule.enabled = True
        schedule.filters = {'id': [9999999]}
        with self.captureOnCommitCallbacks(execute=True):
            schedule.save()
        audit_scheduling.run_due()
        schedule.refresh_from_db()
        self.assertEqual(schedule.runs.get().outcome, 'skipped')
        self.assertFalse(schedule.enabled)
        self.assertIsNone(schedule.next_run_at)

    def test_apply_permission_is_enforced_on_save_and_dispatch(self):
        auditor = get_user_model().objects.create_user('no-change-permission')
        instance = AuditSchedule(run_as=auditor)
        data = {'name': 'No changes', 'enabled': True, 'remediate': True, 'frequency': 'now',
                'window_hours': 1, 'devices': [self.devices[0].pk], 'profile_source': 'custom',
                'features': ['ntp'], 'comparison': 'replace'}
        form = AuditScheduleForm(data, instance=instance)
        self.assertFalse(form.is_valid())
        self.assertIn('permission to apply', str(form.errors))
        schedule = self.schedule(remediate=True)
        self.user.is_superuser = False
        self.user.save()
        AuditSchedule.objects.filter(pk=schedule.pk).update(next_run_at=timezone.now())
        run = audit_scheduling.dispatch(schedule.pk)
        self.assertEqual(run.outcome, 'failed')
        self.assertFalse(UpgradeJob.objects.exists())

    def test_now_api_is_available_and_audit_is_default(self):
        api = APIClient()
        api.force_authenticate(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            response = api.post(reverse('plugins-api:netbox_discovery-api:auditschedule-list'), {
                'name': 'API now', 'frequency': 'now', 'filters': {'id': [self.devices[0].pk]},
                'profile': {'features': ['ntp'], 'mode': 'replace'}}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['remediate'])
        self.assertFalse(UpgradeJob.objects.exists())
        audit_scheduling.run_due()
        schedule = AuditSchedule.objects.get(pk=response.data['id'])
        self.assertFalse(schedule.enabled)
        self.assertEqual(schedule.runs.get().jobs.get().operation, 'audit_config')

    def test_upgrade_form_and_lists_are_separate_from_standards(self):
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_add'))
        form = response.context['form']
        self.assertEqual({value for value, _ in form.fields['operation'].choices}, {'audit', 'stage', 'upgrade'})
        self.assertNotIn('features', form.fields)
        with self.captureOnCommitCallbacks(execute=True):
            schedule = self.schedule(frequency='now')
        audit_scheduling.run_due()
        job = schedule.runs.get().jobs.first()
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_list'))
        self.assertEqual(len(response.context['table'].data), 0)
        response = self.browser.get(reverse('plugins:netbox_discovery:standardsjob_list'))
        self.assertContains(response, 'Standards Jobs')
        self.assertContains(response, 'job_scope=standards')
        self.assertIn(job.pk, [item.pk for item in response.context['table'].data])

    def test_menu_follows_setup_to_execution(self):
        from netbox_discovery.navigation import menu
        group = next(group for group in menu.groups if group.label == 'Software and Standards')
        names = [item.link_text for item in group.items]
        self.assertEqual(names, ['Standards', 'Job Profiles', 'Platform Profiles', 'Model Profiles',
                                'Redundancy Groups', 'Upgrade Dependencies', 'Automatic Image Staging',
                                'Audit Schedules', 'Upgrade Jobs', 'Standards Jobs'])

    def test_bulk_all_standards_cannot_cancel_upgrades(self):
        from .test_upgrades import PROFILE
        upgrade_jobs = queue.schedule(self.user, {**self.data, 'operation': 'audit', 'profile': PROFILE})
        with self.captureOnCommitCallbacks(execute=True):
            schedule = self.schedule(frequency='now')
        audit_scheduling.run_due()
        url = reverse('plugins:netbox_discovery:upgradejob_bulk_cancel') + '?job_scope=standards'
        response = self.browser.post(url, {'_all': 'on'})
        selection = response.context['form'].initial['selection']
        self.assertEqual(set(selection), set(schedule.runs.get().jobs.values_list('pk', flat=True)))
        import json
        response = self.browser.post(url, {'confirm': 'yes', 'selection': json.dumps(selection)})
        self.assertRedirects(response, reverse('plugins:netbox_discovery:standardsjob_list'))
        self.assertEqual(set(UpgradeJob.objects.filter(pk__in=[job.pk for job in upgrade_jobs])
                             .values_list('status', flat=True)), {'pending'})
