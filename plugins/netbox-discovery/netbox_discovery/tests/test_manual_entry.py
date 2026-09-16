"""Manual completion must preserve the reading, even with altered POST data."""

from copy import deepcopy

from dcim.models import Manufacturer, Region, Site
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from extras.models import Tag
from ipam.models import Prefix

from netbox_discovery import actions
from netbox_discovery.choices import OnboardingStatusChoices as C
from netbox_discovery.forms import OnboardingManualEntryForm
from netbox_discovery.models import OnboardingRequest


FINDINGS = {
    'sys_name': 'switch-01', 'sys_descr': 'Original description',
    'credential': 'original-profile',
    'devices': [
        {'name': 'switch-02', 'model': 'MEMBER', 'serial': 'MEMBER-SERIAL'},
        {
            'name': 'switch-01', 'manufacturer': 'Uncatalogued vendor',
            'model': '', 'serial': 'DISCOVERED-SERIAL',
            'platform': 'Uncatalogued OS', 'software_version': '1.2.3',
            'is_master': True, 'vc_position': 1,
            'interfaces': [{'name': 'eth0', 'mac': '00:11:22:33:44:55'}],
            'modules': [{'name': 'Power supply', 'serial': 'PSU-SERIAL'}],
        },
    ],
    'access_points': [{'name': 'ap-01'}],
}


class ManualEntryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(name='Manual US', slug='manual-us')
        cls.site = Site.objects.create(name='Manual Site', slug='manual-site',
                                       region=region)
        cls.site.tags.add(Tag.objects.create(name='poller-manual', slug='poller-manual'))
        Prefix.objects.create(prefix='198.51.100.0/24', scope=cls.site)
        cls.manufacturer = Manufacturer.objects.create(name='Manual vendor', slug='manual-vendor')
        cls.user = get_user_model().objects.create_superuser('manual', password='x')

    def setUp(self):
        self.entry = OnboardingRequest(address='198.51.100.10')
        self.entry.save()
        self.entry.status = C.STATUS_FAILED
        self.entry.discovered = deepcopy(FINDINGS)
        self.entry.save()
        self.client.force_login(self.user)

    def test_page_prefills_and_locks_observed_fields_without_catalog_records(self):
        response = self.client.get(self.entry.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        form = response.context['manual_form']
        for name in ('name', 'manufacturer', 'serial', 'platform', 'software_version'):
            self.assertEqual(form[name].value(), FINDINGS['devices'][1][name])
            self.assertTrue(form.fields[name].disabled)
        self.assertFalse(form.fields['model'].disabled)
        self.assertNotIn('override_site', form.fields)

    def test_only_missing_fields_need_to_be_posted(self):
        response = self.client.post(reverse(
            'plugins:netbox_discovery:onboardingrequest_manual',
            kwargs={'pk': self.entry.pk}), {'model': 'NEW-MODEL'})
        self.assertEqual(response.status_code, 302)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, C.STATUS_APPROVED)
        self.assertEqual(self.entry.target_site, self.site)
        expected = deepcopy(FINDINGS)
        expected['devices'][1]['model'] = 'NEW-MODEL'
        self.assertEqual(self.entry.discovered, expected)

    def test_forged_form_values_cannot_change_discovered_information(self):
        self.entry.discovered['devices'][1]['model'] = 'OBSERVED-MODEL'
        self.entry.save()
        expected = deepcopy(self.entry.discovered)
        response = self.client.post(reverse(
            'plugins:netbox_discovery:onboardingrequest_manual',
            kwargs={'pk': self.entry.pk}), {
                field: 'WRONG' for field in (
                    'name', 'manufacturer', 'model', 'serial', 'platform', 'software_version',
                )
            })
        self.assertEqual(response.status_code, 302)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, C.STATUS_APPROVED)
        self.assertEqual(self.entry.discovered, expected)
        self.assertEqual(self.entry.override_name, 'switch-01')

    def test_api_cannot_replace_observed_values_or_other_devices(self):
        response = self.client.post(reverse(
            'plugins-api:netbox_discovery-api:onboardingrequest-manual',
            kwargs={'pk': self.entry.pk}), {
                'name': 'WRONG', 'manufacturer': 'WRONG', 'model': 'NEW-MODEL',
                'serial': 'WRONG', 'platform': 'WRONG', 'software_version': 'WRONG',
            }, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.data)
        self.entry.refresh_from_db()
        expected = deepcopy(FINDINGS)
        expected['devices'][1]['model'] = 'NEW-MODEL'
        self.assertEqual(self.entry.discovered, expected)

    def test_sys_name_is_locked_when_no_device_name_was_reported(self):
        del self.entry.discovered['devices'][1]['name']
        form = OnboardingManualEntryForm(data={'model': 'NEW-MODEL'}, entry=self.entry)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.fields['name'].disabled)
        self.assertEqual(form.cleaned_data['name'], 'switch-01')
        actions.enter_manually(self.entry, **form.cleaned_data)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.primary_discovered['name'], 'switch-01')

    def test_empty_scan_still_accepts_a_fully_manual_device(self):
        self.entry.discovered = {}
        form = OnboardingManualEntryForm(data={
            'name': 'manual-device', 'manufacturer': self.manufacturer.pk,
            'model': 'MANUAL-MODEL',
        }, entry=self.entry)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(any(field.disabled for field in form.fields.values()))
        actions.enter_manually(self.entry, **form.cleaned_data)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, C.STATUS_APPROVED)
        self.assertEqual(self.entry.primary_discovered['manufacturer'], 'Manual vendor')
        self.assertEqual(self.entry.primary_discovered['interfaces'], [])

    def test_missing_required_values_are_still_rejected(self):
        form = OnboardingManualEntryForm(data={}, entry=self.entry)
        self.assertFalse(form.is_valid())
        self.assertIn('model', form.errors)

    def test_no_site_does_not_approve_or_discard_the_scan(self):
        self.entry.site = None
        with self.assertRaisesMessage(actions.TransitionError, 'site assignment'):
            actions.enter_manually(self.entry, name='WRONG', manufacturer='WRONG',
                                   model='NEW-MODEL')
        self.assertEqual(self.entry.discovered, FINDINGS)
        self.assertEqual(self.entry.status, C.STATUS_FAILED)


class ManualEntryIsProtectedTest(TestCase):
    """A device created from a hand-entered request is tagged so the scanner
    never changes what was typed."""

    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(name='Tagged US', slug='tagged-us')
        cls.site = Site.objects.create(name='Tagged Site', slug='tagged-site', region=region)
        cls.site.tags.add(Tag.objects.create(name='poller-tagged', slug='poller-tagged'))
        Prefix.objects.create(prefix='198.51.100.0/24', scope=cls.site)
        cls.user = get_user_model().objects.create_superuser('tagged', password='x')
        from dcim.models import DeviceRole, DeviceType
        manufacturer = Manufacturer.objects.create(name='Tagged vendor', slug='tagged-vendor')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='T-1', slug='t-1')
        role = DeviceRole.objects.create(name='Tagged', slug='tagged')
        from dcim.models import Device
        cls.device = Device.objects.create(name='typed-01', device_type=device_type,
                                           role=role, site=cls.site)

    def applied(self, manually_entered):
        entry = OnboardingRequest(address='198.51.100.20')
        entry.save()
        entry.manually_entered = manually_entered
        entry.status = C.STATUS_APPROVED
        entry.save()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('plugins-api:netbox_discovery-api:onboardingrequest-applied',
                    kwargs={'pk': entry.pk}),
            {'ok': True, 'device': self.device.pk}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content[:500])
        return {t.slug for t in self.device.tags.all()}

    def test_a_hand_entered_device_is_tagged_when_applied(self):
        self.assertIn('discovery-manual', self.applied(manually_entered=True))

    def test_a_scanned_device_is_not(self):
        self.assertNotIn('discovery-manual', self.applied(manually_entered=False))
