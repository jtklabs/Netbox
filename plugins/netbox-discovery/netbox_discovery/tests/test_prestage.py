"""Prestage policies: the standard's preferred image copied ahead of the upgrade window."""
from datetime import timedelta

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site, VirtualChassis
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from extras.models import Tag
from ipam.models import IPAddress
from netbox_refresh.models import DeviceSoftware, SoftwareStandard, SoftwareVersion
from rest_framework.test import APIClient
from users.models import ObjectPermission

from netbox_discovery import upgrade_prestage as prestage, upgrade_queue as queue
from netbox_discovery.models import DiscoveryPoller, PrestagePolicy, UpgradeJob


class PrestageFixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('prestage-admin', 'p@example.test', 'test-only')
        self.site = Site.objects.create(name='Prestage lab', slug='prestage-lab')
        self.site.tags.add(Tag.objects.create(name='poller-lab', slug='poller-lab'))
        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        self.dtype = DeviceType.objects.create(model='C9350-48P', slug='c9350-48p', manufacturer=manufacturer)
        self.other_type = DeviceType.objects.create(model='C9350-24P', slug='c9350-24p', manufacturer=manufacturer)
        self.platform = Platform.objects.create(name='IOS XE', slug='ios-xe')
        self.role = DeviceRole.objects.create(name='Access', slug='access')
        self.devices = {}
        for index, name in enumerate(('sw1', 'sw2', 'sw3')):
            self.devices[name] = Device.objects.create(
                name=name, site=self.site, role=self.role, device_type=self.dtype, platform=self.platform,
                primary_ip4=IPAddress.objects.create(address=f'192.0.2.{10 + index}/24'))
        self.old = SoftwareVersion.objects.create(platform=self.platform, version='17.18.1')
        self.target = self.version('17.18.4')
        self.standard = SoftwareStandard.objects.create(preferred_version=self.target)
        self.standard.device_types.set([self.dtype])
        self.standard.approved_versions.set([self.target])
        DeviceSoftware.objects.create(device=self.devices['sw1'], software_version=self.old)
        DeviceSoftware.objects.create(device=self.devices['sw2'], software_version=self.target)
        self.policy = PrestagePolicy.objects.create(device_type=self.dtype)

    def version(self, release):
        return SoftwareVersion.objects.create(
            platform=self.platform, version=release, image_filename=f'cisco9k_iosxe.{release}.SPA.bin',
            image_url=f'http://images.example.test/cisco9k_iosxe.{release}.SPA.bin',
            checksum_type='md5', checksum='B' * 32)

    def actions(self, now=None):
        return {row['device'].name: (row['action'], row['detail']) for row in prestage.plan(self.policy, now)}


class PlanTest(PrestageFixture):
    def test_stages_preferred_image_on_devices_not_running_it(self):
        actions = self.actions()
        self.assertEqual(actions['sw1'], ('schedule', 'Stage cisco9k_iosxe.17.18.4.SPA.bin'))
        self.assertEqual(actions['sw2'][0], 'current')
        # A device with no collected version still gets the image.
        self.assertEqual(actions['sw3'][0], 'schedule')

    def test_profile_is_a_staging_profile_from_the_standard(self):
        row = next(r for r in prestage.plan(self.policy) if r['device'].name == 'sw1')
        self.assertEqual(row['profile'], {
            'name': 'prestage-c9350-48p-17.18.4', 'models': ['C9350-48P'], 'starting_versions': [],
            'target_version': '17.18.4', 'image': 'cisco9k_iosxe.17.18.4.SPA.bin', 'md5': 'b' * 32,
            'minimum_free_bytes': 1_500_000_000,
            'image_source': 'http://images.example.test/cisco9k_iosxe.17.18.4.SPA.bin'})
        self.policy.minimum_free_bytes = 3_000_000_000
        row = next(r for r in prestage.plan(self.policy) if r['device'].name == 'sw1')
        self.assertEqual(row['profile']['minimum_free_bytes'], 3_000_000_000)

    def test_skips_exempt_busy_and_untagged_devices(self):
        DeviceSoftware.objects.filter(device=self.devices['sw1']).update(exempt=True)
        now = timezone.now()
        UpgradeJob.objects.create(device=self.devices['sw3'], device_name='sw3', address='192.0.2.12',
                                  poller=DiscoveryPoller.objects.create(name='lab'), profile={}, operation='audit',
                                  scheduled_at=now, start_before=now + timedelta(hours=1))
        actions = self.actions()
        self.assertEqual(actions['sw1'], ('skip', 'Marked do not upgrade in Lifecycle'))
        self.assertEqual(actions['sw3'], ('skip', 'Already has a queued or active upgrade job'))

        self.site.tags.clear()
        UpgradeJob.objects.all().delete()
        DeviceSoftware.objects.filter(device=self.devices['sw1']).update(exempt=False)
        self.assertEqual(self.actions()['sw1'], ('skip', 'No poller tag'))

    def test_version_without_md5_or_url_is_not_staged(self):
        SoftwareVersion.objects.filter(pk=self.target.pk).update(checksum_type='sha512')
        self.assertEqual(self.actions()['sw1'], ('skip', 'IOS XE 17.18.4 has no MD5 checksum'))
        SoftwareVersion.objects.filter(pk=self.target.pk).update(checksum_type='md5', image_url='')
        self.assertEqual(self.actions()['sw1'], ('skip', 'IOS XE 17.18.4 has no image URL'))

    def test_no_standard_means_nothing_to_stage(self):
        self.standard.device_types.clear()
        self.assertEqual(self.actions()['sw1'], ('skip', 'No software standard with a preferred version applies'))

    def test_platform_standard_applies_when_no_model_standard(self):
        self.standard.device_types.clear()
        self.standard.platforms.set([self.platform])
        self.assertEqual(self.actions()['sw1'][0], 'schedule')

    def test_stack_gets_one_job_on_its_master_with_every_member_model(self):
        stack = VirtualChassis.objects.create(name='stack1')
        member = Device.objects.create(name='sw1-2', site=self.site, role=self.role, device_type=self.other_type,
                                       virtual_chassis=stack, vc_position=2)
        Device.objects.filter(pk=self.devices['sw1'].pk).update(virtual_chassis=stack, vc_position=1)
        Device.objects.filter(pk=self.devices['sw3'].pk).update(virtual_chassis=stack, vc_position=3)
        stack.master = self.devices['sw1']
        stack.save()
        rows = {row['device'].name: row for row in prestage.plan(self.policy)}
        self.assertNotIn('sw3', rows)
        self.assertNotIn(member.name, rows)
        self.assertEqual(rows['sw1']['profile']['models'], ['C9350-24P', 'C9350-48P'])

    def test_inactive_and_other_models_are_ignored(self):
        Device.objects.filter(pk=self.devices['sw3'].pk).update(status='planned')
        Device.objects.create(name='other', site=self.site, role=self.role, device_type=self.other_type,
                              primary_ip4=IPAddress.objects.create(address='192.0.2.50/24'))
        self.assertEqual(set(self.actions()), {'sw1', 'sw2'})


class RunTest(PrestageFixture):
    def test_run_schedules_staging_jobs_in_one_batch_and_records_the_result(self):
        jobs, summary = prestage.run_policy(self.policy)
        self.assertEqual(sorted(job.device_name for job in jobs), ['sw1', 'sw3'])
        self.assertEqual({job.operation for job in jobs}, {'stage'})
        self.assertEqual(len({job.batch_id for job in jobs}), 1)
        self.assertEqual({job.poller.name for job in jobs}, {'lab'})
        self.assertEqual(jobs[0].groups, [])
        self.assertEqual(jobs[0].start_before - jobs[0].scheduled_at, timedelta(hours=4))
        self.assertEqual((summary['scheduled'], summary['current'], summary['skipped']), (2, 1, 0))
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.last_summary['scheduled'], 2)
        self.assertIsNotNone(self.policy.last_run_at)

    def test_rerun_waits_out_the_interval_for_the_same_image(self):
        now = timezone.now()
        jobs, _ = prestage.run_policy(self.policy, now)
        UpgradeJob.objects.filter(pk__in=[job.pk for job in jobs]).update(status='completed', completed_at=now)
        jobs, summary = prestage.run_policy(self.policy, now + timedelta(hours=1))
        self.assertEqual(jobs, [])
        self.assertEqual(summary['reasons'], {'Given this image within the interval': 2})
        jobs, _ = prestage.run_policy(self.policy, now + timedelta(hours=25))
        self.assertEqual(len(jobs), 2)

    def test_changing_the_standard_stages_the_new_image_on_the_next_run(self):
        now = timezone.now()
        jobs, _ = prestage.run_policy(self.policy, now)
        UpgradeJob.objects.filter(pk__in=[job.pk for job in jobs]).update(status='completed', completed_at=now)
        newer = self.version('17.18.5')
        self.standard.approved_versions.add(newer)
        self.standard.preferred_version = newer
        self.standard.save()
        jobs, _ = prestage.run_policy(self.policy, now + timedelta(hours=1))
        # sw2 ran the old preferred version; it now gets the new one too.
        self.assertEqual(sorted(job.device_name for job in jobs), ['sw1', 'sw2', 'sw3'])
        self.assertEqual({job.profile['image'] for job in jobs}, {'cisco9k_iosxe.17.18.5.SPA.bin'})

    def test_disabled_policies_do_not_run(self):
        self.policy.enabled = False
        self.policy.save()
        self.assertEqual(prestage.run(), {})
        self.assertFalse(UpgradeJob.objects.exists())

    def test_worker_claims_prestage_jobs_only_with_apply(self):
        prestage.run()
        poller = DiscoveryPoller.objects.get(name='lab')
        self.assertEqual(queue.claim(self.user, poller, 10, False), [])
        claimed = queue.claim(self.user, poller, 10, True)
        self.assertEqual(len(claimed), 2)
        self.assertEqual(queue.assignment(claimed[0])['profile']['starting_versions'], [])

    def test_prestage_job_can_be_requeued(self):
        jobs, _ = prestage.run_policy(self.policy)
        UpgradeJob.objects.filter(pk=jobs[0].pk).update(status='failed', completed_at=timezone.now())
        job = queue.requeue(self.user, jobs[0].pk)
        self.assertEqual((job.operation, job.profile['starting_versions']), ('stage', []))


class ProfileValidationTest(TestCase):
    def test_empty_starting_versions_only_for_staging(self):
        profile = {'name': 'p', 'models': ['C9350-48P'], 'starting_versions': [], 'target_version': '17.18.4',
                   'image': 'cisco9k_iosxe.17.18.4.SPA.bin', 'md5': 'a' * 32, 'minimum_free_bytes': 1500000000}
        queue.validate_profile(profile, 'stage')
        for operation in ('audit', 'upgrade', None):
            with self.assertRaises(queue.QueueError):
                queue.validate_profile(profile, operation)


class PermissionTest(PrestageFixture):
    def setUp(self):
        super().setUp()
        self.operator = get_user_model().objects.create_user('operator', 'o@example.test', 'test-only')
        permission = ObjectPermission.objects.create(name='policies', actions=['view', 'add', 'change'])
        permission.object_types.add(*(ContentType.objects.get_for_model(model) for model in (PrestagePolicy, UpgradeJob)))
        permission.users.add(self.operator)

    def test_policy_changes_need_apply_permission(self):
        client = APIClient()
        client.force_authenticate(self.operator)
        url = reverse('plugins-api:netbox_discovery-api:prestagepolicy-list')
        response = client.post(url, {'device_type': self.other_type.pk}, format='json')
        self.assertEqual(response.status_code, 403, response.content)
        response = client.post(reverse('plugins-api:netbox_discovery-api:prestagepolicy-run'), {}, format='json')
        self.assertEqual(response.status_code, 403)

        browser = Client()
        browser.force_login(self.operator)
        self.assertEqual(browser.get(reverse('plugins:netbox_discovery:prestagepolicy_add')).status_code, 403)

    def test_admin_creates_and_runs_policy_through_the_api(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(reverse('plugins-api:netbox_discovery-api:prestagepolicy-list'),
                               {'device_type': self.other_type.pk, 'interval_hours': 12}, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        response = client.post(reverse('plugins-api:netbox_discovery-api:prestagepolicy-run'),
                               {'policy': self.policy.pk}, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['C9350-48P']['scheduled'], 2)


class ViewTest(PrestageFixture):
    def test_detail_page_shows_the_next_run(self):
        client = Client()
        client.force_login(self.user)
        response = client.get(self.policy.get_absolute_url())
        self.assertContains(response, 'Stage cisco9k_iosxe.17.18.4.SPA.bin')
        self.assertContains(response, 'Already running 17.18.4')
        self.assertContains(response, '2 to stage')
        self.assertEqual(client.get(reverse('plugins:netbox_discovery:prestagepolicy_list')).status_code, 200)

    def test_run_now_buttons(self):
        client = Client()
        client.force_login(self.user)
        client.post(reverse('plugins:netbox_discovery:prestagepolicy_run', args=[self.policy.pk]))
        self.assertEqual(UpgradeJob.objects.filter(operation='stage').count(), 2)
        response = client.post(reverse('plugins:netbox_discovery:prestagepolicy_run_all'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(UpgradeJob.objects.filter(operation='stage').count(), 2)
