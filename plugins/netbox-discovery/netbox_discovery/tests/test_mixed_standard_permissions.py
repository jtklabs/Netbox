"""Mixed profiles preserve each standard's change permissions through scheduling."""
from django.test import TestCase
from django.utils import timezone
from netbox_compliance.definitions import snapshot_for_device
from netbox_compliance.models import ConfigStandard

from netbox_discovery import audit_scheduling, upgrade_queue as queue
from netbox_discovery.audit_views import AuditScheduleForm
from netbox_discovery.models import AuditSchedule, JobProfile, UpgradeJob
from netbox_discovery.quick_run import run_now
from . import test_remediation


class MixedStandardPermissionsTest(TestCase):
    def setUp(self):
        test_remediation.RemediationTest.setUp(self)
        self.device = self.devices[0]
        self.ntp = ConfigStandard.objects.get(name='NTP')
        self.ntp.auto_remediable = self.ntp.allow_enforce = False
        self.ntp.save()
        self.data.update(filters={'id': [self.device.pk]},
                         profile={'features': ['ntp', 'syslog'], 'mode': 'replace'})

    def test_mixed_profile_pins_permissions_without_changing_profile_mode(self):
        job = queue.schedule(self.user, self.data)[0]
        self.assertEqual(job.operation, 'remediate')
        self.assertEqual(job.profile['mode'], 'replace')
        self.assertEqual(job.standards_snapshot['read_only_features'], ['ntp'])
        self.ntp.auto_remediable = self.ntp.allow_enforce = True
        self.ntp.save()
        job.refresh_from_db()
        self.assertEqual(job.standards_snapshot['read_only_features'], ['ntp'])

    def test_add_permission_does_not_grant_replace_permission(self):
        self.ntp.auto_remediable = True
        self.ntp.save()
        for mode, readonly in (('add', []), ('replace', ['ntp'])):
            with self.subTest(mode=mode):
                snap = snapshot_for_device(self.user, self.device, ['ntp', 'syslog'], mode)
                self.assertEqual(snap['read_only_features'], readonly)

    def test_raw_check_without_commands_is_read_only_even_with_permissive_flags(self):
        standard = ConfigStandard.objects.create(name='NTP associations', check_type='netops',
            auto_remediable=True, allow_enforce=True, definition_yaml='''raw_commands:
  enabled: true
  platforms:
    cisco_ios:
      show: show ntp associations
      reject: [INIT]
''')
        snap = snapshot_for_device(self.user, self.device, ['raw_commands', 'syslog'], 'replace',
                                   raw_standard_ids=[standard.pk])
        self.assertEqual(snap['read_only_features'], [f'raw_commands:{standard.pk}'])

    def test_auto_profile_keeps_writable_standards_enabled(self):
        profile = JobProfile.objects.create(name='Mixed profile', kind='remediate', plan=self.data['profile'])
        job = run_now(self.user, self.device, f'profile:{profile.pk}', 'auto')
        self.assertEqual(job.operation, 'remediate')
        self.assertEqual(job.standards_snapshot['read_only_features'], ['ntp'])

    def test_all_read_only_auto_profile_becomes_an_audit(self):
        profile = JobProfile.objects.create(name='Read-only profile', kind='remediate',
                                             plan={'features': ['ntp'], 'mode': 'replace'})
        job = run_now(self.user, self.device, f'profile:{profile.pk}', 'auto')
        self.assertEqual(job.operation, 'audit_config')
        self.assertEqual(job.standards_snapshot['read_only_features'], ['ntp'])

    def test_recurring_remediation_schedule_accepts_mixed_permissions(self):
        form = AuditScheduleForm({'name': 'Mixed recurring schedule', 'enabled': True,
            'frequency': 'daily', 'window_hours': 1, 'local_time': '02:00', 'time_zone': 'UTC',
            'devices': [self.device.pk], 'profile_source': 'custom', 'remediate': True,
            'features': ['ntp', 'syslog'], 'comparison': 'replace'},
            instance=AuditSchedule(run_as=self.user))
        self.assertTrue(form.is_valid(), form.errors)
        schedule = form.save()
        AuditSchedule.objects.filter(pk=schedule.pk).update(next_run_at=timezone.now())
        audit_scheduling.run_due()
        job = UpgradeJob.objects.get()
        self.assertEqual(job.operation, 'remediate')
        self.assertEqual(job.standards_snapshot['read_only_features'], ['ntp'])

    def test_ready_gate_requires_exact_read_only_acknowledgment(self):
        queue.schedule(self.user, self.data)
        job = queue.claim(self.user, self.poller, 1, True)[0]
        data = {'claim_token': job.claim_token, 'sequence': 1, 'stage': 'ready', 'message': 'Apply syslog',
                'summary': {'standards_revisions': [
                    {'standard_id': p['standard_id'], 'revision': p['revision']}
                    for p in job.standards_snapshot['revisions']]}}
        for ack in (None, [], ['syslog'], ['ntp', 'syslog']):
            with self.subTest(ack=ack), self.assertRaisesRegex(queue.QueueError, 'read-only standards'):
                queue.report(self.user, job.pk, {**data, 'summary': {**data['summary'], 'read_only_features': ack}})
        job.refresh_from_db()
        self.assertIsNone(job.started_at)
        data['summary']['read_only_features'] = ['ntp']
        self.assertEqual(queue.report(self.user, job.pk, data).status, 'running')
