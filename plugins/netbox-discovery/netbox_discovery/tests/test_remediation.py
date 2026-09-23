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
                                                   'summary': {'features': {'ntp': 'changed'}}})
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
