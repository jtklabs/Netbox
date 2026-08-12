"""Supplying a model the device does not report.

Some platforms publish no model at all — a Firepower 2120 among them — and
without one there is no device type, so nothing can be created. The scan is
otherwise fine: serial, version, interfaces and addresses all arrive. Losing
all of that because one field is missing is the wrong trade, so a reviewer can
type the model and everything else the scan found is kept.
"""

from dcim.models import Region, Site
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from extras.models import Tag
from ipam.models import Prefix

from netbox_discovery import actions
from netbox_discovery.choices import OnboardingStatusChoices as C
from netbox_discovery.models import OnboardingRequest

NO_MODEL = {
    'devices': [{
        'name': 'fw-dal-01', 'model': '', 'serial': 'JAD12345678',
        'manufacturer': 'Cisco', 'is_master': True,
        'software_version': '7.2.5', 'interfaces': [], 'modules': [],
    }],
}
WITH_MODEL = {
    'devices': [dict(NO_MODEL['devices'][0], model='FPR-2120')],
}


@override_settings(PLUGINS_CONFIG={'netbox_discovery': {'default_region': 'mo-us'}})
class ModelOverrideTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.region = Region.objects.create(name='MO US', slug='mo-us')
        cls.region.tags.add(Tag.objects.create(name='poller-mo', slug='poller-mo'))
        cls.site = Site.objects.create(name='MO Site', slug='mo-site',
                                       region=cls.region)
        Prefix.objects.create(prefix='198.51.100.0/24', scope=cls.site)
        User = get_user_model()
        cls.user = User.objects.create_user('mo', password='x')
        cls.user.is_superuser = True
        cls.user.save()

    def request_for(self, findings):
        entry = OnboardingRequest(address='198.51.100.10')
        entry.save()
        entry.discovered = findings
        entry.status = C.STATUS_REVIEW
        entry.save()
        return entry

    def test_without_a_model_there_is_nothing_to_create(self):
        entry = self.request_for(NO_MODEL)
        self.assertEqual(entry.effective_model, '')
        with self.assertRaises(actions.TransitionError) as caught:
            actions.approve(entry)
        self.assertIn('no model', str(caught.exception))

    def test_it_is_refused_at_approval_not_left_to_the_poller(self):
        """The poller would create nothing, report a failure, and the person
        who could have typed the model would be long gone."""
        entry = self.request_for(NO_MODEL)
        with self.assertRaises(actions.TransitionError):
            actions.approve(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_REVIEW)

    def test_approving_with_a_model_supplied_works(self):
        entry = self.request_for(NO_MODEL)
        actions.approve(entry, override_model='FPR-2120')
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_APPROVED)
        self.assertEqual(entry.effective_model, 'FPR-2120')

    def test_everything_else_the_scan_found_is_kept(self):
        """The point of an override rather than typing the device by hand."""
        entry = self.request_for(NO_MODEL)
        actions.approve(entry, override_model='FPR-2120')
        entry.refresh_from_db()
        device = entry.primary_discovered
        self.assertEqual(device['serial'], 'JAD12345678')
        self.assertEqual(device['software_version'], '7.2.5')

    def test_what_the_device_reports_still_wins(self):
        """An override left on a request must not quietly override a later
        scan that does read a model."""
        entry = self.request_for(WITH_MODEL)
        entry.override_model = 'WRONG-1'
        entry.save()
        self.assertEqual(entry.effective_model, 'FPR-2120')

    # The poller-side half of this lives in the scanner's own suite
    # (scripts/snmp-inventory/tests/test_onboarding_overrides.py): snmpinv is
    # not installed in the NetBox container, and importing it here made these
    # error rather than fail, which is a worse kind of red.

    def test_the_review_form_offers_it(self):
        entry = self.request_for(NO_MODEL)
        self.client.force_login(self.user)
        response = self.client.get(reverse(
            'plugins:netbox_discovery:onboardingrequest',
            kwargs={'pk': entry.pk}))
        self.assertIn('override_model', response.context['review_form'].fields)

    def test_approving_over_the_api_accepts_it_too(self):
        entry = self.request_for(NO_MODEL)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('plugins-api:netbox_discovery-api:onboardingrequest-approve',
                    kwargs={'pk': entry.pk}),
            {'override_model': 'FPR-2120'}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.data)
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_APPROVED)

    def test_the_job_handed_to_the_poller_carries_it(self):
        """Without this the poller re-reads the scan, finds no model again,
        and creates nothing — the override having changed only the database."""
        entry = self.request_for(NO_MODEL)
        actions.approve(entry, override_model='FPR-2120')
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('plugins-api:netbox_discovery-api:discoverypoller-check-in'),
            {'name': 'mo'}, content_type='application/json')
        jobs = response.data['jobs']
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['override_model'], 'FPR-2120')
