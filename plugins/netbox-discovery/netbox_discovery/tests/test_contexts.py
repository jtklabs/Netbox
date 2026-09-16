"""A scan that is a partition of a chassis: a Nexus VDC or a vCMP guest.

The scanner works out what it is and how to write it; this is the NetBox
half — taking the report, not mistaking a VDC's chassis serial for a clash,
stopping when the chassis is missing, and saying on the page what applying
will do.
"""

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Region, Site
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from extras.models import Tag
from ipam.models import Prefix
from rest_framework.test import APIClient, APITestCase

from netbox_discovery import review
from netbox_discovery.choices import OnboardingStatusChoices as C
from netbox_discovery.models import OnboardingRequest

CHASSIS_SERIAL = 'JAF1234ABCD'
VDC = {'kind': 'vdc', 'chassis_serial': CHASSIS_SERIAL, 'name': 'dmz', 'identifier': 2,
       'detail': "VDC 2 'dmz' of the chassis with serial JAF1234ABCD"}
GUEST = {'kind': 'vcmp-guest', 'chassis_serial': 'chs123456s', 'name': 'ltm-guest-01',
         'identifier': None,
         'detail': 'vCMP guest (platform Z101) on the host with chassis serial chs123456s'}


class ContextsTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(name='Ctx US', slug='ctx-us')
        cls.site = Site.objects.create(name='Ctx Site', slug='ctx-site', region=region)
        cls.site.tags.add(Tag.objects.create(name='poller-ctx', slug='poller-ctx'))
        Prefix.objects.create(prefix='198.51.100.0/24', scope=cls.site)
        cls.user = get_user_model().objects.create_superuser('ctx', password='x')
        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model='N7K-C7010', slug='n7k-c7010')
        cls.role = DeviceRole.objects.create(name='Network', slug='network')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.request = OnboardingRequest(address='198.51.100.12')
        self.request.save()

    def chassis(self, name='n7k-1', serial=CHASSIS_SERIAL):
        return Device.objects.create(name=name, serial=serial, site=self.site,
                                     device_type=self.device_type, role=self.role)

    def _report(self, context, name='n7k-1-dmz', serial=CHASSIS_SERIAL):
        return self.client.post(
            reverse('plugins-api:netbox_discovery-api:onboardingrequest-scanned',
                    kwargs={'pk': self.request.pk}),
            {'ok': True, 'sys_name': name,
             'devices': [{'name': name, 'model': 'N7K-C7010', 'serial': serial,
                          'manufacturer': 'Cisco', 'platform': 'Cisco NX-OS',
                          'is_master': True, 'context': context}]},
            format='json',
        )

    # --- review

    def test_a_vdc_whose_chassis_is_in_netbox_is_not_a_serial_clash(self):
        self.chassis()
        needs_review, reason = review.evaluate(self.request, {'devices': [
            {'name': 'n7k-1-dmz', 'model': 'N7K-C7010', 'serial': CHASSIS_SERIAL,
             'context': VDC, 'is_master': True}]})
        self.assertFalse(needs_review, reason)

    def test_a_vdc_whose_chassis_is_missing_waits_for_it(self):
        needs_review, reason = review.evaluate(self.request, {'devices': [
            {'name': 'n7k-1-dmz', 'model': 'N7K-C7010', 'serial': CHASSIS_SERIAL,
             'context': VDC, 'is_master': True}]})
        self.assertTrue(needs_review)
        self.assertIn('Onboard the chassis first', reason)
        self.assertIn("VDC 2 'dmz'", reason)

    def test_a_chassis_serial_on_a_box_of_its_own_is_still_a_clash(self):
        """The old rule, untouched for anything that is not a VDC."""
        self.chassis()
        needs_review, reason = review.evaluate(self.request, {'devices': [
            {'name': 'something-else', 'model': 'N7K-C7010', 'serial': CHASSIS_SERIAL,
             'context': None, 'is_master': True}]})
        self.assertTrue(needs_review)
        self.assertIn('already on', reason)

    def test_a_guest_reports_no_serial_and_is_not_held(self):
        needs_review, reason = review.evaluate(self.request, {'devices': [
            {'name': 'ltm-guest-01', 'model': 'BIG-IP vCMP Guest', 'serial': '',
             'context': GUEST, 'is_master': True}]})
        self.assertFalse(needs_review, reason)

    # --- the report and the page

    def test_the_report_keeps_the_context(self):
        self.chassis()
        response = self._report(VDC)
        self.assertEqual(response.status_code, 200, response.data)
        self.request.refresh_from_db()
        self.assertEqual(self.request.discovered_context, VDC)
        self.assertEqual(self.request.status, C.STATUS_APPROVED)

    def test_an_older_poller_sends_none(self):
        response = self._report(None, name='n9k-1', serial='OTHER1')
        self.assertEqual(response.status_code, 200, response.data)
        self.request.refresh_from_db()
        self.assertIsNone(self.request.discovered_context)

    def test_the_page_names_the_chassis_and_what_applying_does(self):
        chassis = self.chassis()
        self._report(VDC)
        web = Client()
        web.force_login(self.user)
        response = web.get(self.request.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Partition of')
        self.assertContains(response, chassis.get_absolute_url())
        self.assertContains(response, 'creates a virtual device context on it')

    def test_the_page_says_when_the_chassis_is_missing(self):
        self._report(VDC)
        web = Client()
        web.force_login(self.user)
        response = web.get(self.request.get_absolute_url())
        self.assertContains(response, 'onboard its default VDC first')

    def test_the_page_describes_a_guest(self):
        self._report(GUEST, name='ltm-guest-01', serial='')
        web = Client()
        web.force_login(self.user)
        response = web.get(self.request.get_absolute_url())
        self.assertContains(response, 'vCMP guest (platform Z101)')
        self.assertContains(response, 'host not in NetBox yet')
