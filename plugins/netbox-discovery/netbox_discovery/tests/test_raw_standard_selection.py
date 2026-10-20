from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from dcim.models import Site
from netbox_compliance.definitions import snapshot_for_device
from netbox_compliance.models import ConfigStandard

from netbox_discovery import upgrade_queue as queue, audit_scheduling
from netbox_discovery.audit_views import AuditScheduleForm
from netbox_discovery.models import AuditSchedule, JobProfile, UpgradeJob
from netbox_discovery.profile_views import JobProfileForm
from netbox_discovery.quick_run import choices_for, run_now
from netbox_discovery.upgrade_views import plan_initial
from .test_remediation import RemediationTest


def raw_standard(name, command):
    return ConfigStandard.objects.create(name=name, check_type='netops', auto_remediable=True,
        allow_enforce=True, definition_yaml=f"""raw_commands:
  enabled: true
  platforms:
    cisco_ios:
      commands: [{command}]
      expect: [{command}]
""")


class RawStandardSelectionTest(TestCase):
    def setUp(self):
        RemediationTest.setUp(self)
        self.first = raw_standard('SSH integrity', 'ip ssh server algorithm mac hmac-sha2-512')
        self.second = raw_standard('Disable HTTP', 'no ip http server')
        self.device = self.devices[0]
        self.plan = {'features': ['raw_commands'], 'mode': 'replace', 'raw_standard_id': self.first.pk}
        self.browser = Client()
        self.browser.force_login(self.user)

    def schedule(self, plan=None, operation='audit_config'):
        return queue.schedule(self.user, {**self.data, 'filters': {'id': [self.device.pk]},
            'operation': operation, 'profile_source': 'custom', 'profile': plan or self.plan})[0]

    def test_only_selected_standard_is_pinned_for_audit_and_apply(self):
        for operation in ('audit_config', 'remediate'):
            with self.subTest(operation=operation):
                job = self.schedule(operation=operation)
                self.assertEqual([p['standard_id'] for p in job.standards_snapshot['revisions']], [self.first.pk])
                self.assertEqual(set(job.standards_snapshot['document']), {'raw_commands'})
                self.assertNotIn('no ip http server', str(job.standards_snapshot))
                self.assertEqual(plan_initial(job)['raw_standard'], self.first.pk)
                self.assertEqual(queue.assignment(job)['profile']['raw_standard_id'], self.first.pk)
        before = job.standards_snapshot
        self.first.definition_yaml = self.first.definition_yaml.replace('hmac-sha2-512', 'hmac-sha2-256')
        self.first.save()
        job.refresh_from_db()
        self.assertEqual(job.standards_snapshot, before)

    def test_automatic_selection_blocks_ambiguity_and_unrelated_jobs_ignore_raw(self):
        with self.assertRaisesRegex(queue.QueueError, 'Raw command standard'):
            self.schedule({'features': ['raw_commands'], 'mode': 'replace'})
        job = self.schedule({'features': ['ntp'], 'mode': 'add'})
        self.assertEqual(set(job.standards_snapshot['document']), {'ntp'})
        self.second.valid_to = timezone.localdate() - timedelta(days=1)
        self.second.save()
        job = self.schedule({'features': ['raw_commands'], 'mode': 'replace'})
        self.assertEqual(job.standards_snapshot['revisions'][0]['standard_id'], self.first.pk)

    def test_invalid_deleted_expired_or_out_of_scope_selection_never_falls_back(self):
        for selected in (999999, ConfigStandard.objects.get(name='NTP').pk):
            with self.assertRaises(queue.QueueError):
                self.schedule({**self.plan, 'raw_standard_id': selected})
        other_site = Site.objects.create(name='Other raw site', slug='other-raw-site')
        self.first.sites.add(other_site)
        with self.assertRaises(queue.QueueError):
            self.schedule()
        self.first.sites.clear()
        self.first.valid_to = timezone.localdate() - timedelta(days=1)
        self.first.save()
        with self.assertRaises(queue.QueueError):
            self.schedule()
        self.assertFalse(UpgradeJob.objects.exists())

    def test_selected_standard_still_requires_visibility_and_enforcement(self):
        hidden = get_user_model().objects.create_user('no-raw-access')
        with self.assertRaises(ValidationError):
            snapshot_for_device(hidden, self.device, ['raw_commands'], 'replace', audit=True,
                                raw_standard_id=self.first.pk)
        self.first.allow_enforce = False
        self.first.save()
        with self.assertRaises(queue.QueueError):
            self.schedule(operation='remediate')
        self.assertEqual(self.schedule().operation, 'audit_config')
        form = JobProfileForm(user=hidden)
        self.assertEqual(list(form.fields['raw_standard'].choices), [('', 'Automatic (only when one applies)')])

    def test_profile_picker_saves_and_restores_only_named_raw_choices(self):
        data = {'name': 'SSH only', 'kind': 'remediate', 'features': ['raw_commands'],
                'remediation_mode': 'replace', 'raw_standard': self.first.pk}
        form = JobProfileForm(data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save()
        self.assertEqual(profile.plan, self.plan)
        edit = JobProfileForm(instance=profile, user=self.user)
        self.assertEqual(edit.initial['raw_standard'], self.first.pk)
        self.assertEqual({value for value, label in edit.fields['raw_standard'].choices},
                         {'', self.first.pk, self.second.pk})
        self.assertContains(self.browser.get(reverse('plugins:netbox_discovery:jobprofile_edit', args=[profile.pk])),
                            'Raw command standard')
        job = queue.schedule(self.user, {**self.data, 'operation': 'audit_config',
            'filters': {'id': [self.device.pk]}, 'profile_source': 'saved', 'profile': None,
            'saved_profile': profile.pk})[0]
        self.assertEqual(job.profile, self.plan)
        for changes in ({'raw_standard': 999999}, {'features': ['ntp']},
                        {'raw_standard': ConfigStandard.objects.get(name='NTP').pk}):
            form = JobProfileForm({**data, **changes}, user=self.user)
            self.assertFalse(form.is_valid())

    def test_single_standard_quick_run_selects_the_clicked_raw_standard(self):
        options = choices_for(self.user, self.device)
        option = next(o for o in options if o['value'] == f'standard:{self.second.pk}')
        self.assertEqual(option['plan']['raw_standard_id'], self.second.pk)
        job = run_now(self.user, self.device, option['value'], 'audit')
        self.assertEqual(job.standards_snapshot['revisions'][0]['standard_id'], self.second.pk)
        self.assertNotIn('hmac-sha2-512', str(job.standards_snapshot))

    def test_custom_schedule_form_roundtrips_the_selection(self):
        schedule = AuditSchedule(run_as=self.user)
        data = {'name': 'Only SSH', 'enabled': True, 'frequency': 'now', 'window_hours': 1,
            'devices': [self.device.pk], 'profile_source': 'custom', 'features': ['raw_commands'],
            'comparison': 'replace', 'raw_standard': self.first.pk}
        form = AuditScheduleForm(data, instance=schedule)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.profile, self.plan)
        self.assertEqual(AuditScheduleForm(instance=saved).initial['raw_standard'], self.first.pk)
        audit_scheduling.run_due()
        job = UpgradeJob.objects.get()
        self.assertEqual(job.operation, 'audit_config')
        self.assertEqual(job.standards_snapshot['revisions'][0]['standard_id'], self.first.pk)

    def test_plan_schema_rejects_unrelated_or_invalid_standard_ids(self):
        for value in (True, 0, -1, '1', None, []):
            with self.subTest(value=value), self.assertRaises(queue.QueueError):
                queue.validate_remediation({**self.plan, 'raw_standard_id': value})
        with self.assertRaises(queue.QueueError):
            queue.validate_remediation({**self.plan, 'features': ['ntp']})
