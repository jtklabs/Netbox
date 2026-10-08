from datetime import datetime, timedelta, timezone as dt_timezone

from dcim.models import Platform, Region
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from tenancy.models import Tenant
import yaml

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import UpgradeJob
from netbox_discovery.upgrade_views import ScheduleForm
from .test_upgrades import UpgradeFixture, PROFILE


class DeviceJobScheduleTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()
        self.browser = Client()
        self.browser.force_login(self.user)
        self.url = reverse('plugins:netbox_discovery:upgradejob_add')

    def form_data(self, **changes):
        return {'devices': [self.device.pk], 'operation': 'audit', 'profile_source': 'custom',
                'profile': yaml.safe_dump(PROFILE), 'time_zone': 'America/New_York',
                'scheduled_at': '2030-07-08T10:30:00', 'start_before': '2030-07-08T14:30:00', **changes}

    def test_labels_and_native_pickers(self):
        response = self.browser.get(self.url)
        self.assertContains(response, 'type="datetime-local"', count=2)
        self.assertContains(response, 'name="time_zone"')
        for name in ('regions', 'tenants', 'sites', 'roles', 'platforms', 'models', 'device_tags', 'devices'):
            self.assertContains(response, f'name="{name}"')
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_list'))
        self.assertContains(response, 'Upgrade Jobs')

    def test_selected_zone_becomes_correct_utc_in_preview_and_saved_job(self):
        response = self.browser.post(self.url, self.form_data())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['preview']), 1)
        self.assertFalse(UpgradeJob.objects.exists())
        response = self.browser.post(self.url, self.form_data(schedule='yes'))
        self.assertEqual(response.status_code, 302)
        job = UpgradeJob.objects.get()
        self.assertEqual(job.scheduled_at, datetime(2030, 7, 8, 14, 30, tzinfo=dt_timezone.utc))
        self.assertEqual(job.start_before - job.scheduled_at, timedelta(hours=4))

    def test_dst_gaps_and_folds_are_not_guessed(self):
        for start in ('2030-03-10T02:30:00', '2030-11-03T01:30:00'):
            form = ScheduleForm(self.form_data(scheduled_at=start))
            self.assertFalse(form.is_valid())
            self.assertIn('daylight saving', str(form.errors['scheduled_at']))
        form = ScheduleForm(self.form_data(scheduled_at='2030-11-03T06:30:00', time_zone='UTC'))
        self.assertTrue(form.is_valid(), form.errors)

    def test_explicit_offset_payloads_and_invalid_zone(self):
        form = ScheduleForm(self.form_data(scheduled_at='2030-07-08T10:30:00+02:00'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['scheduled_at'].utcoffset(), timedelta(hours=2))
        form = ScheduleForm(self.form_data(time_zone='Not/AZone'))
        self.assertFalse(form.is_valid())
        self.assertIn('time_zone', form.errors)

    def test_scope_filters_intersect_and_include_child_regions(self):
        parent = Region.objects.create(name='Americas', slug='americas')
        self.site.region = Region.objects.create(name='East', slug='east', parent=parent)
        self.site.save()
        self.device.tenant = Tenant.objects.create(name='Team', slug='team')
        self.device.platform = Platform.objects.create(name='IOS', slug='ios')
        self.device.save()
        tag = self.site.tags.first()
        self.device.tags.add(tag)
        data = self.form_data(devices=[], regions=[parent.pk], tenants=[self.device.tenant_id],
                              sites=[self.site.pk], roles=[self.role.pk], platforms=[self.device.platform_id],
                              models=[self.device.device_type_id], device_tags=[tag.pk])
        form = ScheduleForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        payload = form.schedule_data()
        self.assertEqual(set(payload['filters']), {'status', 'region_id', 'tenant_id', 'site_id', 'role_id',
                                                  'platform_id', 'device_type_id', 'tag_id'})
        self.assertEqual([row['device'].pk for row in queue.prepare(self.user, payload)], [self.device.pk])
        other = Tenant.objects.create(name='Other', slug='other')
        form = ScheduleForm({**data, 'tenants': [other.pk]})
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(queue.QueueError):
            queue.prepare(self.user, form.schedule_data())

    def test_empty_scope_requires_explicit_all_active(self):
        form = ScheduleForm(self.form_data(devices=[]))
        self.assertFalse(form.is_valid())
        self.assertIn('all_active', form.errors)
        form = ScheduleForm(self.form_data(all_active=True))
        self.assertFalse(form.is_valid())
        form = ScheduleForm(self.form_data(devices=[], all_active=True))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.schedule_data()['filters'], {'status': ['active']})
        self.device.status = 'offline'
        self.device.save()
        with self.assertRaises(queue.QueueError):
            queue.prepare(self.user, form.schedule_data())

    def test_old_single_scope_fields_and_model_prefill_still_work(self):
        form = ScheduleForm(self.form_data(devices=[], device_type=self.device.device_type_id))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.schedule_data()['filters']['device_type_id'], [self.device.device_type_id])
        response = self.browser.get(self.url, {'device_type': self.device.device_type_id})
        self.assertEqual(response.context['form'].initial['models'], [self.device.device_type_id])

    @override_settings(TIME_ZONE='America/New_York')
    def test_edit_initial_is_local_and_roundtrips(self):
        job = self.scheduled(scheduled_at=datetime(2030, 7, 8, 14, 30, tzinfo=dt_timezone.utc),
                             start_before=datetime(2030, 7, 8, 18, 30, tzinfo=dt_timezone.utc))
        url = reverse('plugins:netbox_discovery:upgradejob_edit', args=[job.pk])
        response = self.browser.get(url)
        self.assertContains(response, 'value="2030-07-08T10:30:00"')
        self.assertContains(response, 'Edit device job')
        response = self.browser.post(url, self.form_data(last_updated=job.last_updated.isoformat()))
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.scheduled_at, datetime(2030, 7, 8, 14, 30, tzinfo=dt_timezone.utc))

    def test_requeue_prefills_native_datetime_inputs(self):
        job = self.scheduled()
        response = self.browser.get(self.url, {'from_job': job.pk})
        form = response.context['form']
        self.assertIsInstance(form.initial['scheduled_at'], datetime)
        self.assertTrue(timezone.is_naive(form.initial['scheduled_at']))
        self.assertContains(response, 'type="datetime-local"', count=2)
