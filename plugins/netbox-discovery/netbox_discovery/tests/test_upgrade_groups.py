"""Redundancy groups: one member at a time, downstream before upstream, and holds that a person releases."""
import uuid
from datetime import timedelta

from dcim.models import Cable, Device, DeviceRole, DeviceType, Interface, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from extras.models import Tag
from ipam.models import FHRPGroup, FHRPGroupAssignment, IPAddress
from rest_framework.test import APIClient

from netbox_discovery import upgrade_groups, upgrade_queue as queue
from netbox_discovery.models import DiscoveryPoller, UpgradeDependency, UpgradeGroup, UpgradeJob
from .test_upgrades import PROFILE


class GroupFixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('group-admin', 'g@example.test', 'test-only')
        self.site = Site.objects.create(name='Groups lab', slug='groups-lab')
        self.site.tags.add(Tag.objects.create(name='poller-lab', slug='poller-lab'))
        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        dtype = DeviceType.objects.create(model='C9350-48P', slug='c9350-48p', manufacturer=manufacturer)
        self.roles = {slug: DeviceRole.objects.create(name=slug.title(), slug=slug) for slug in ('core', 'access')}
        self.devices = {}
        for index, (name, role) in enumerate((('sw3a', 'access'), ('sw3b', 'access'), ('core-a', 'core'), ('core-b', 'core'))):
            self.devices[name] = Device.objects.create(
                name=name, site=self.site, role=self.roles[role], device_type=dtype,
                primary_ip4=IPAddress.objects.create(address=f'192.0.2.{10 + index}/24'))
        self.closet = UpgradeGroup.objects.create(name='closet-3')
        self.closet.members.set([self.devices['sw3a'], self.devices['sw3b']])
        self.core = UpgradeGroup.objects.create(name='core-pair')
        self.core.members.set([self.devices['core-a'], self.devices['core-b']])
        for upstream in ('core-a', 'core-b'):
            for downstream in ('sw3a', 'sw3b'):
                UpgradeDependency.objects.create(upstream=self.devices[upstream], downstream=self.devices[downstream])
        self.poller = DiscoveryPoller.objects.create(name='lab')
        now = timezone.now()
        self.data = {'filters': {'site': ['groups-lab']}, 'profile': dict(PROFILE), 'operation': 'audit',
                     'scheduled_at': now - timedelta(minutes=1), 'start_before': now + timedelta(hours=2)}

    def schedule(self, **overrides):
        return {job.device_name: job for job in queue.schedule(self.user, {**self.data, **overrides})}

    def claim(self, limit=10):
        return [job.device_name for job in queue.claim(self.user, self.poller, limit, True)]

    def report(self, job, stage, seq=1):
        job.refresh_from_db()
        return queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': seq, 'stage': stage, 'message': stage})


class WaveTest(GroupFixture):
    def test_preview_orders_closets_before_cores_one_member_at_a_time(self):
        rows = queue.prepare(self.user, self.data)
        waves = {str(row['device']): row['wave'] for row in rows}
        self.assertEqual(waves, {'sw3a': 1, 'sw3b': 2, 'core-a': 3, 'core-b': 4})
        jobs = self.schedule()
        self.assertEqual(jobs['sw3b'].planned_wave, 2)
        self.assertEqual(jobs['sw3b'].groups, ['closet-3'])
        self.assertEqual(sorted(jobs['core-a'].waits_for), sorted([self.devices['sw3a'].pk, self.devices['sw3b'].pk]))

    def test_a_wider_limit_lets_members_share_a_wave(self):
        self.closet.max_concurrent = 2
        self.closet.save()
        waves = {str(row['device']): row['wave'] for row in queue.prepare(self.user, self.data)}
        self.assertEqual(waves['sw3a'], waves['sw3b'])

    def test_cycles_are_refused(self):
        UpgradeDependency.objects.create(upstream=self.devices['sw3a'], downstream=self.devices['core-a'])
        with self.assertRaisesMessage(queue.QueueError, 'cycle'):
            queue.prepare(self.user, self.data)
        self.assertFalse(UpgradeJob.objects.exists())


class ClaimOrderTest(GroupFixture):
    def test_queue_serializes_pairs_and_waits_for_downstream(self):
        jobs = self.schedule()
        self.assertEqual(self.claim(), ['sw3a'])
        self.assertEqual(self.claim(), [])
        self.report(jobs['sw3a'], 'dry_run_complete')
        self.assertEqual(self.claim(), ['sw3b'])
        self.report(jobs['sw3b'], 'dry_run_complete')
        self.assertEqual(self.claim(), ['core-a'])
        self.report(jobs['core-a'], 'dry_run_complete')
        self.assertEqual(self.claim(), ['core-b'])

    def test_capacity_is_global_across_batches(self):
        first = self.schedule(filters={'name': ['sw3a']})
        second = self.schedule(filters={'name': ['sw3b']})
        self.assertEqual(self.claim(), ['sw3a'])
        self.assertEqual(self.claim(), [])
        self.report(first['sw3a'], 'dry_run_complete')
        self.assertEqual(self.claim(), ['sw3b'])
        self.assertEqual(second['sw3b'].planned_wave, 1)

    def test_ordering_is_batch_scoped(self):
        self.schedule(filters={'name': ['core-a']})
        self.schedule(filters={'name': ['sw3a']}, scheduled_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.claim(), ['core-a'])


class HoldTest(GroupFixture):
    def test_failure_holds_partner_and_dependents_until_released(self):
        jobs = self.schedule()
        self.claim()
        self.report(jobs['sw3a'], 'blocked')
        for name in ('sw3b', 'core-a', 'core-b'):
            jobs[name].refresh_from_db()
            self.assertEqual(jobs[name].status, 'held', name)
            self.assertIn('sw3a ended failed in this batch', jobs[name].held_reason)
        self.assertEqual(self.claim(), [])
        with self.assertRaises(queue.QueueError):
            queue.release(self.user, jobs['sw3b'].pk, '   ')
        queue.release(self.user, jobs['sw3b'].pk, 'sw3a only failed flash space; verified')
        jobs['sw3b'].refresh_from_db()
        self.assertEqual(jobs['sw3b'].status, 'pending')
        self.assertIn('Released', jobs['sw3b'].message)
        self.assertEqual(self.claim(), ['sw3b'])

    def test_release_batch_and_manual_hold(self):
        jobs = self.schedule()
        self.claim()
        self.report(jobs['sw3a'], 'blocked')
        self.assertEqual(queue.release_batch(self.user, jobs['sw3a'].batch_id, 'checked'), 3)
        self.assertEqual(UpgradeJob.objects.filter(status='held').count(), 0)
        queue.hold(self.user, jobs['sw3b'].pk, 'wait for the change window')
        jobs['sw3b'].refresh_from_db()
        self.assertEqual((jobs['sw3b'].status, jobs['sw3b'].held_reason), ('held', 'wait for the change window'))
        self.assertEqual(self.claim(), [])

    def test_recovery_required_fences_partners_across_batches(self):
        first = self.schedule(filters={'name': ['sw3a']}, operation='upgrade')
        self.claim()
        self.report(first['sw3a'], 'ready')
        self.report(first['sw3a'], 'failed', 2)
        first['sw3a'].refresh_from_db()
        self.assertEqual(first['sw3a'].status, 'recovery_required')
        later = self.schedule(filters={'name': ['sw3b', 'core-a']})
        self.assertEqual(self.claim(), [])
        for name in ('sw3b', 'core-a'):
            later[name].refresh_from_db()
            self.assertEqual(later[name].status, 'held', name)
            self.assertIn('requires recovery', later[name].held_reason)

    def test_plain_failure_does_not_reach_other_batches(self):
        first = self.schedule(filters={'name': ['sw3a']})
        self.claim()
        self.report(first['sw3a'], 'blocked')
        self.schedule(filters={'name': ['sw3b']})
        self.assertEqual(self.claim(), ['sw3b'])

    def test_held_jobs_expire_with_their_window(self):
        jobs = self.schedule()
        self.claim()
        self.report(jobs['sw3a'], 'blocked')
        UpgradeJob.objects.filter(pk=jobs['sw3b'].pk).update(start_before=timezone.now() - timedelta(seconds=1))
        self.claim()
        jobs['sw3b'].refresh_from_db()
        self.assertEqual(jobs['sw3b'].status, 'expired')


class DiscoveryTest(GroupFixture):
    def interface(self, device, name):
        return Interface.objects.create(device=device, name=name, type='1000base-t')

    def test_fhrp_groups_and_cables_become_groups_and_dependencies(self):
        for group in UpgradeGroup.objects.all():
            group.delete()
        UpgradeDependency.objects.all().delete()
        fhrp = FHRPGroup.objects.create(protocol='hsrp', group_id=10)
        interface_type = ContentType.objects.get_for_model(Interface)
        for name in ('core-a', 'core-b'):
            FHRPGroupAssignment.objects.create(group=fhrp, interface_type=interface_type,
                                               interface_id=self.interface(self.devices[name], 'Vlan10').pk, priority=100)
        uplink, downlink = self.interface(self.devices['sw3a'], 'Te1/1/1'), self.interface(self.devices['core-a'], 'Te1/0/3')
        Cable(a_terminations=[uplink], b_terminations=[downlink]).save()
        Cable(a_terminations=[self.interface(self.devices['core-a'], 'Te1/0/4')],
              b_terminations=[self.interface(self.devices['core-b'], 'Te1/0/4')]).save()
        counts = upgrade_groups.refresh_discovered()
        self.assertEqual((counts['groups'], counts['dependencies']), (1, 1))
        group = UpgradeGroup.objects.get(source='fhrp')
        self.assertEqual(set(group.members.values_list('name', flat=True)), {'core-a', 'core-b'})
        self.assertEqual(group.key, f'fhrp:{fhrp.pk}')
        dependency = UpgradeDependency.objects.get()
        self.assertEqual((dependency.upstream.name, dependency.downstream.name, dependency.source), ('core-a', 'sw3a', 'cable'))
        # A second refresh updates in place; removing the FHRP assignments marks the group stale.
        upgrade_groups.refresh_discovered()
        self.assertEqual(UpgradeGroup.objects.count(), 1)
        FHRPGroupAssignment.objects.all().delete()
        upgrade_groups.refresh_discovered()
        group.refresh_from_db()
        self.assertTrue(group.stale)
        manual = UpgradeGroup.objects.create(name='by-hand')
        upgrade_groups.refresh_discovered()
        manual.refresh_from_db()
        self.assertFalse(manual.stale)


class GroupApiAndUiTest(GroupFixture):
    def test_hold_release_and_groups_over_the_api(self):
        jobs = self.schedule()
        self.claim()
        self.report(jobs['sw3a'], 'blocked')
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(f'/api/plugins/discovery/upgrade-jobs/{jobs["sw3b"].pk}/release/', {'reason': 'checked'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'pending')
        response = client.post(f'/api/plugins/discovery/upgrade-jobs/{jobs["sw3b"].pk}/hold/', {'reason': 'wait'}, format='json')
        self.assertEqual(response.data['status'], 'held')
        response = client.post('/api/plugins/discovery/upgrade-jobs/release-batch/', {'batch_id': str(jobs['sw3a'].batch_id), 'reason': 'go'}, format='json')
        self.assertEqual(response.data['released'], 3)
        response = client.post('/api/plugins/discovery/upgrade-groups/', {'name': 'ilb-pool-1', 'max_concurrent': 2,
                                                                          'members': [self.devices['sw3a'].pk]}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['source'], 'manual')
        response = client.get(f'/api/plugins/discovery/upgrade-jobs/{jobs["core-a"].pk}/')
        self.assertEqual(response.data['groups'], ['core-pair'])
        self.assertEqual(client.post('/api/plugins/discovery/upgrade-groups/refresh/', {}, format='json').status_code, 200)

    def test_pages_render_and_show_release_controls(self):
        jobs = self.schedule()
        self.claim()
        self.report(jobs['sw3a'], 'blocked')
        browser = Client()
        browser.force_login(self.user)
        for url in (reverse('plugins:netbox_discovery:upgradegroup_list'), self.closet.get_absolute_url(),
                    reverse('plugins:netbox_discovery:upgradegroup_add'), reverse('plugins:netbox_discovery:upgradedependency_list'),
                    UpgradeDependency.objects.first().get_absolute_url(), reverse('plugins:netbox_discovery:upgradejob_list')):
            self.assertEqual(browser.get(url).status_code, 200, url)
        response = browser.get(jobs['sw3b'].get_absolute_url())
        self.assertContains(response, 'Release this job')
        self.assertContains(response, 'held jobs in this batch')
        response = browser.post(reverse('plugins:netbox_discovery:upgradejob_release', args=[jobs['sw3b'].pk]),
                                {'reason': 'verified', 'scope': 'batch'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(UpgradeJob.objects.filter(status='held').count(), 0)
        response = browser.post(reverse('plugins:netbox_discovery:upgradegroup_refresh'))
        self.assertEqual(response.status_code, 302)


class OfficeTest(GroupFixture):
    """An office: every access switch at once, the core pair afterwards and never alongside them."""

    def setUp(self):
        super().setUp()
        UpgradeDependency.objects.all().delete()
        self.closet.name, self.closet.max_concurrent = 'office-access', 0
        self.closet.save()
        self.core.name = 'office-core'
        self.core.save()
        self.core.depends_on.add(self.closet)
        dtype = DeviceType.objects.get(slug='c9350-48p')
        self.devices['sw4'] = Device.objects.create(name='sw4', site=self.site, role=self.roles['access'], device_type=dtype,
                                                    primary_ip4=IPAddress.objects.create(address='192.0.2.40/24'))

    def test_access_goes_together_and_core_waits_for_all_of_it(self):
        rows = queue.prepare(self.user, self.data)
        waves = {str(row['device']): row['wave'] for row in rows}
        self.assertEqual(waves, {'sw3a': 1, 'sw3b': 1, 'sw4': 1, 'core-a': 2, 'core-b': 3})
        jobs = self.schedule()
        self.assertEqual(sorted(jobs['core-a'].waits_for), sorted([self.devices['sw3a'].pk, self.devices['sw3b'].pk]))
        self.assertEqual(sorted(self.claim()), ['sw3a', 'sw3b', 'sw4'])
        self.assertEqual(self.claim(), [])
        for name in ('sw3a', 'sw3b', 'sw4'):
            self.report(jobs[name], 'dry_run_complete')
        self.assertEqual(self.claim(), ['core-a'])

    def test_groups_that_wait_on_each_other_never_run_together_across_batches(self):
        access = self.schedule(filters={'name': ['sw3a']})
        self.assertEqual(self.claim(), ['sw3a'])
        self.schedule(filters={'name': ['core-a']})
        self.assertEqual(self.claim(), [])
        self.report(access['sw3a'], 'dry_run_complete')
        self.assertEqual(self.claim(), ['core-a'])
        self.schedule(filters={'name': ['sw3b']})
        self.assertEqual(self.claim(), [])

    def test_a_group_cannot_wait_for_itself(self):
        from netbox_discovery.upgrade_views import UpgradeGroupForm
        form = UpgradeGroupForm(instance=self.core, data={'name': 'office-core', 'max_concurrent': 1,
                                                          'depends_on': [self.core.pk]})
        self.assertFalse(form.is_valid())
        self.assertIn('depends_on', form.errors)

    def test_failure_holds_the_rest_of_the_site_in_the_batch(self):
        jobs = self.schedule()
        self.assertEqual(self.claim(limit=1), ['sw3a'])
        self.report(jobs['sw3a'], 'blocked')
        for name in ('sw3b', 'sw4', 'core-a', 'core-b'):
            jobs[name].refresh_from_db()
            self.assertEqual(jobs[name].status, 'held', name)
        self.assertIn('at the same site in this batch', jobs['sw4'].held_reason)
        self.assertIn('ended failed in this batch', jobs['core-a'].held_reason)
        queue.release(self.user, jobs['sw4'].pk, 'unrelated closet, checked')
        self.assertEqual(self.claim(), ['sw4'])

    def test_site_hold_can_be_switched_off(self):
        from django.test import override_settings
        with override_settings(PLUGINS_CONFIG={'netbox_discovery': {'hold_site_on_failure': False}}):
            jobs = self.schedule(filters={'name': ['sw3a', 'sw4']})
            self.assertEqual(self.claim(limit=1), ['sw3a'])
            self.report(jobs['sw3a'], 'blocked')
            jobs['sw4'].refresh_from_db()
            self.assertEqual(jobs['sw4'].status, 'pending')
            self.assertEqual(self.claim(), ['sw4'])
