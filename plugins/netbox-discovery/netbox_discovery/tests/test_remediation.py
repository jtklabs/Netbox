"""Queued configuration remediation: features and a mode, applied from each poller's standards.yaml."""
from datetime import timedelta

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from extras.models import Tag
from ipam.models import IPAddress

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import DiscoveryPoller, UpgradeDependency, UpgradeGroup, UpgradeJob

from .test_upgrades import PROFILE


class RemediationTest(TestCase):
    def setUp(self):
        from netbox_compliance.tests.factories import feature_standards
        feature_standards()
        self.user = get_user_model().objects.create_superuser('fix-admin', 'f@example.test', 'test-only')
        self.site = Site.objects.create(name='Fix lab', slug='fix-lab')
        self.site.tags.add(Tag.objects.create(name='poller-lab', slug='poller-lab'))
        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        dtype = DeviceType.objects.create(model='C9300-48P', slug='c9300-48p', manufacturer=manufacturer)
        role = DeviceRole.objects.create(name='Access', slug='access')
        self.devices = [Device.objects.create(name=f'sw{i}', site=self.site, role=role, device_type=dtype,
                                              primary_ip4=IPAddress.objects.create(address=f'192.0.2.{20 + i}/24'))
                        for i in range(2)]
        group = UpgradeGroup.objects.create(name='pair')
        group.members.set(self.devices)
        UpgradeDependency.objects.create(upstream=self.devices[1], downstream=self.devices[0])
        self.poller = DiscoveryPoller.objects.create(name='lab')
        now = timezone.now()
        self.data = {'filters': {'site': ['fix-lab']}, 'operation': 'remediate',
                     'profile': {'features': ['ntp', 'syslog'], 'mode': 'add'},
                     'scheduled_at': now - timedelta(minutes=1), 'start_before': now + timedelta(hours=2)}

    def test_profile_is_features_and_mode_only(self):
        for bad in ({'features': ['ntp']}, {'features': [], 'mode': 'add'}, {'features': ['nac'], 'mode': 'add'},
                    {'features': ['ntp'], 'mode': 'enforce'}, {'features': ['ntp'], 'mode': 'add', 'image': 'x'}, PROFILE):
            with self.assertRaises(queue.QueueError):
                queue.validate_profile(bad, 'remediate')
        # And an upgrade profile check never accepts a remediation plan.
        with self.assertRaises(queue.QueueError):
            queue.validate_profile(self.data['profile'], 'upgrade')

    def test_remediation_ignores_redundancy_groups_and_runs_together(self):
        jobs = queue.schedule(self.user, self.data)
        self.assertEqual({(job.groups == [], job.waits_for == [], job.planned_wave) for job in jobs}, {(True, True, 1)})
        claimed = queue.claim(self.user, self.poller, 10, True)
        self.assertEqual(len(claimed), 2)
        self.assertEqual(queue.assignment(claimed[0])['profile'], {'features': ['ntp', 'syslog'], 'mode': 'add'})

    def test_needs_apply_on_both_sides(self):
        queue.schedule(self.user, self.data)
        self.assertEqual(queue.claim(self.user, self.poller, 10, False), [])

    def test_failure_does_not_hold_the_rest_of_the_site(self):
        jobs = queue.schedule(self.user, {**self.data, 'filters': {'id': [self.devices[0].pk]}})
        later = queue.schedule(self.user, {**self.data, 'filters': {'id': [self.devices[1].pk]}})
        # Same batch for the site rule: put them together.
        UpgradeJob.objects.filter(pk=later[0].pk).update(batch_id=jobs[0].batch_id)
        job = queue.claim(self.user, self.poller, 1, True)[0]
        queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': 1, 'stage': 'failed', 'message': 'x'})
        other = UpgradeJob.objects.exclude(pk=job.pk).get()
        self.assertEqual(other.status, 'pending')

    def test_ready_gate_and_outcomes(self):
        queue.schedule(self.user, {**self.data, 'filters': {'id': [self.devices[0].pk]}})
        job = queue.claim(self.user, self.poller, 1, True)[0]
        for sequence, stage in enumerate(('precheck_complete', 'ready', 'completed'), start=1):
            job = queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': sequence,
                                                   'stage': stage, 'message': stage,
                                                   'summary': {'features': {'ntp': 'changed'},
                                                               'standards_revisions': [
                                                                   {'standard_id': p['standard_id'], 'revision': p['revision']}
                                                                   for p in job.standards_snapshot['revisions']]}})
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.summary['features'], {'ntp': 'changed'})

    def test_schedule_form_builds_the_plan_from_checkboxes(self):
        client = Client()
        client.force_login(self.user)
        url = reverse('plugins:netbox_discovery:upgradejob_add')
        now = timezone.now()
        response = client.post(url, {'devices': [self.devices[0].pk], 'operation': 'remediate',
                                     'scheduled_at': now.isoformat(), 'start_before': (now + timedelta(hours=1)).isoformat(),
                                     'features': ['syslog', 'ntp'], 'remediation_mode': 'replace', 'schedule': 'yes'})
        self.assertEqual(response.status_code, 302, response.content[:2000])
        job = UpgradeJob.objects.get()
        self.assertEqual(job.profile, {'features': ['ntp', 'syslog'], 'mode': 'replace'})

        response = client.post(url, {'devices': [self.devices[1].pk], 'operation': 'remediate',
                                     'scheduled_at': now.isoformat(), 'start_before': (now + timedelta(hours=1)).isoformat(),
                                     'remediation_mode': 'add', 'schedule': 'yes'})
        self.assertContains(response, 'Choose at least one remediation feature')

    def test_form_opens_with_devices_from_the_grid(self):
        client = Client()
        client.force_login(self.user)
        response = client.get(reverse('plugins:netbox_discovery:upgradejob_add'),
                              {'operation': 'remediate', 'devices': [self.devices[0].pk, self.devices[1].pk]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['devices'], [self.devices[0].pk, self.devices[1].pk])
        self.assertEqual(response.context['form'].initial['operation'], 'remediate')

    def test_pending_remediation_can_be_edited(self):
        job = queue.schedule(self.user, {**self.data, 'filters': {'id': [self.devices[0].pk]}})[0]
        client = Client()
        client.force_login(self.user)
        url = reverse('plugins:netbox_discovery:upgradejob_edit', args=[job.pk])
        form = client.get(url).context['form']
        self.assertEqual(form.initial['features'], ['ntp', 'syslog'])
        response = client.post(url, {'operation': 'remediate', 'scheduled_at': job.scheduled_at.isoformat(),
                                     'start_before': job.start_before.isoformat(), 'features': ['ntp'],
                                     'remediation_mode': 'add', 'last_updated': job.last_updated.isoformat()})
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.profile, {'features': ['ntp'], 'mode': 'add'})

    def test_standards_are_pinned_and_a_changed_standard_blocks_ready(self):
        from netbox_compliance.models import ConfigStandard
        job = queue.schedule(self.user, {**self.data, 'filters': {'id': [self.devices[0].pk]}})[0]
        self.assertEqual(job.standards_snapshot['document']['ntp']['servers'], ['192.0.2.10'])
        job = queue.claim(self.user, self.poller, 1, True)[0]
        standard = ConfigStandard.objects.get(name='NTP')
        standard.definition_yaml = 'ntp:\n  servers: [192.0.2.11]'
        standard.save()
        with self.assertRaisesRegex(queue.QueueError, 'standard changed'):
            queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': 1,
                                            'stage': 'ready', 'message': 'Apply'})
        job.refresh_from_db()
        self.assertIsNone(job.started_at)

    def test_feature_checks_write_revision_aware_compliance(self):
        from netbox_compliance.models import ConfigCompliance
        queue.schedule(self.user, {**self.data, 'filters': {'id': [self.devices[0].pk]}})
        job = queue.claim(self.user, self.poller, 1, True)[0]
        queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': 1,
                                        'stage': 'precheck_complete', 'message': 'Checked',
                                        'summary': {'compliance_results': {'ntp': 'non-compliant', 'syslog': 'compliant'}}})
        result = ConfigCompliance.objects.get(device=job.device, standard__name='NTP')
        self.assertEqual(result.result, 'non-compliant')
        self.assertEqual(result.checked_revision, 1)

    def test_old_worker_cannot_apply_local_standards(self):
        queue.schedule(self.user, {**self.data, 'filters': {'id': [self.devices[0].pk]}})
        job = queue.claim(self.user, self.poller, 1, True)[0]
        with self.assertRaisesRegex(queue.QueueError, 'acknowledge'):
            queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': 1,
                                            'stage': 'ready', 'message': 'Applying'})

    def test_read_only_audit_claims_without_apply_and_records_revision_results(self):
        from netbox_compliance.models import ConfigCompliance, ConfigStandard
        for standard in ConfigStandard.objects.all():
            standard.auto_remediable = False
            standard.allow_enforce = False
            standard.save()
        data = {**self.data, 'operation': 'audit_config', 'profile': {'features': ['ntp'], 'mode': 'replace'}}
        jobs = queue.schedule(self.user, data)
        self.assertTrue(all(not job.groups and not job.waits_for for job in jobs))
        job = queue.claim(self.user, self.poller, 1, False)[0]
        with self.assertRaisesRegex(queue.QueueError, 'cannot authorize changes'):
            queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': 1,
                                            'stage': 'ready', 'message': 'Not allowed'})
        queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': 1,
                                        'stage': 'completed_with_warnings', 'message': 'Read-only: NTP drift',
                                        'summary': {'compliance_results': {'ntp': 'non-compliant'}}})
        job.refresh_from_db()
        self.assertIsNone(job.started_at)
        self.assertEqual(job.status, 'completed_with_warnings')
        result = ConfigCompliance.objects.get(device=job.device, standard__name='NTP')
        self.assertEqual(result.result, 'non-compliant')
        self.assertEqual(result.checked_revision, job.standards_snapshot['revisions'][0]['revision'])

    def test_audit_form_selects_standards_and_reuses_remediation_profiles(self):
        from netbox_discovery.models import DeviceTypeProfile, JobProfile
        from netbox_discovery.upgrade_views import UpgradePlanForm, plan_initial
        profile = JobProfile.objects.create(name='Time audit', kind='remediate',
                                             plan={'features': ['ntp'], 'mode': 'replace'})
        DeviceTypeProfile.objects.create(device_type=self.devices[0].device_type, remediation_profile=profile)
        job = queue.schedule(self.user, {**self.data, 'profile': None, 'profile_source': 'model',
                                         'operation': 'audit_config'})[0]
        self.assertEqual(job.profile_name, profile.name)
        self.assertEqual(plan_initial(job)['features'], ['ntp'])
        form = UpgradePlanForm(data={'operation': 'audit_config', 'profile_source': 'custom',
                                      'features': ['ntp'], 'remediation_mode': 'replace',
                                      'scheduled_at': self.data['scheduled_at'],
                                      'start_before': self.data['start_before']})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['profile'], profile.plan)

    def test_audit_permissions_do_not_allow_switching_to_remediation(self):
        from django.contrib.contenttypes.models import ContentType
        from users.models import ObjectPermission
        user = get_user_model().objects.create_user('audit-only')
        from netbox_compliance.models import ConfigStandard
        for model, actions in ((Device, ['view']), (ConfigStandard, ['view']),
                               (UpgradeJob, ['add', 'view', 'change', 'run'])):
            perm = ObjectPermission.objects.create(name=f'audit-{model.__name__}', actions=actions)
            perm.object_types.add(ContentType.objects.get_for_model(model))
            perm.users.add(user)
        job = queue.schedule(user, {**self.data, 'operation': 'audit_config'})[0]
        with self.assertRaisesRegex(queue.QueueError, 'apply permission'):
            queue.edit_pending(user, job.pk, {**self.data, 'last_updated': job.last_updated})
        job.refresh_from_db()
        self.assertEqual(job.operation, 'audit_config')
