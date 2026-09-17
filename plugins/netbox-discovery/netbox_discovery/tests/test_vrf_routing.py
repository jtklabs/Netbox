"""Overlapping address space: the VRF says which network is meant.

10.x exists here and again at the company bought last year. NetBox holds the
duplicate in a VRF, and the request's VRF picks the routing table that places
the address: with a VRF, only that VRF's prefixes decide the site and the
poller; without one, only the global table does. Never a mixture, and an
address that is not in the chosen table is refused rather than handed to the
default region's poller, which would go after a different device.
"""

from dcim.models import Region, Site
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from extras.models import Tag
from ipam.models import VRF, IPAddress, Prefix
from rest_framework.test import APIClient
from tenancy.models import Tenant

from netbox_discovery.forms import OnboardingRequestForm
from netbox_discovery.models import OnboardingRequest
from netbox_discovery.resolution import resolve


@override_settings(PLUGINS_CONFIG={'netbox_discovery': {'default_region': 'vr-us'}})
class VrfRoutingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(name='VR US', slug='vr-us')
        region.tags.add(Tag.objects.create(name='poller-main', slug='poller-main'))
        cls.hq = Site.objects.create(name='HQ', slug='vr-hq', region=region)
        cls.acquired = Site.objects.create(name='Acquired HQ', slug='vr-acq', region=region)
        cls.acquired.tags.add(Tag.objects.create(name='poller-remote', slug='poller-remote'))

        cls.tenant = Tenant.objects.create(name='Acquired Co', slug='acquired-co')
        cls.vrf = VRF.objects.create(name='ACQ', tenant=cls.tenant)
        cls.empty_vrf = VRF.objects.create(name='EMPTY')

        # The same /24 twice: ours in the global table, theirs in their VRF.
        # Theirs carries no tenant of its own -- the ordinary way it gets
        # forgotten -- and the VRF still says whose it is.
        Prefix.objects.create(prefix='10.10.1.0/24', scope=cls.hq)
        Prefix.objects.create(prefix='10.10.1.0/24', vrf=cls.vrf, scope=cls.acquired)
        # Space that exists only over there.
        Prefix.objects.create(prefix='10.77.0.0/24', vrf=cls.vrf, scope=cls.acquired)
        cls.user = get_user_model().objects.create_superuser('vrf-routing', password='x')

    # --- which table places the address

    def test_no_vrf_means_the_global_table(self):
        found = resolve('10.10.1.5')
        self.assertEqual((found.site, found.poller_name), (self.hq, 'main'))
        self.assertIsNone(found.prefix.vrf)

    def test_a_vrf_means_that_vrfs_prefixes(self):
        found = resolve('10.10.1.5', vrf=self.vrf)
        self.assertEqual((found.site, found.poller_name), (self.acquired, 'remote'))
        self.assertEqual(found.prefix.vrf, self.vrf)

    def test_the_tenant_comes_from_the_vrf_when_the_prefix_has_none(self):
        self.assertEqual(resolve('10.10.1.5', vrf=self.vrf).tenant, self.tenant)

    def test_a_tenant_narrows_within_the_vrf_even_for_a_tenantless_prefix(self):
        found = resolve('10.10.1.5', tenant=self.tenant, vrf=self.vrf)
        self.assertEqual(found.site, self.acquired)

    def test_a_tenant_alone_does_not_reach_into_a_vrf(self):
        """The VRF picks the table; the tenant only narrows within it."""
        found = resolve('10.77.0.5', tenant=self.tenant)
        self.assertFalse(found.ok)
        self.assertEqual(found.needs, 'vrf')

    # --- what is refused rather than guessed

    def test_an_address_only_in_a_vrf_is_not_handed_to_the_default_poller(self):
        """The default region is configured and tagged, so falling back would
        'work' -- and send the main poller after whatever answers at that
        address in its own network."""
        found = resolve('10.77.0.5')
        self.assertFalse(found.ok)
        self.assertFalse(found.used_default_region)
        self.assertEqual(found.needs, 'vrf')
        self.assertIn('VRF ACQ', found.problem)
        self.assertIn('Choose the VRF', found.problem)

    def test_a_vrf_with_no_prefix_for_the_address_is_refused_not_fallen_back(self):
        found = resolve('10.10.1.5', vrf=self.empty_vrf)
        self.assertFalse(found.ok)
        self.assertIn('VRF EMPTY has no prefix containing 10.10.1.5', found.problem)

    def test_an_address_in_no_table_at_all_still_falls_back(self):
        found = resolve('203.0.113.9')
        self.assertTrue(found.ok)
        self.assertTrue(found.used_default_region)

    def test_nesting_under_another_owners_aggregate_is_not_ambiguity(self):
        """A tenantless /8 over a tenant's /24 used to be refused as
        'different owners'. The longest mask in one table simply wins."""
        ours = Tenant.objects.create(name='Us', slug='us-tenant')
        Prefix.objects.create(prefix='10.0.0.0/8')
        Prefix.objects.create(prefix='10.20.0.0/24', tenant=ours, scope=self.hq)
        found = resolve('10.20.0.5')
        self.assertTrue(found.ok, found.problem)
        self.assertEqual((found.site, found.tenant), (self.hq, ours))

    # --- the form, the request and the job

    def test_the_form_puts_the_error_on_the_vrf_field(self):
        form = OnboardingRequestForm(data={'address': '10.77.0.5'})
        self.assertFalse(form.is_valid())
        self.assertIn('vrf', form.errors)

    def test_the_form_accepts_it_with_the_vrf(self):
        form = OnboardingRequestForm(data={'address': '10.77.0.5', 'vrf': self.vrf.pk})
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_request_is_filed_for_the_vrfs_poller_and_tenant(self):
        entry = OnboardingRequest(address='10.10.1.5', vrf=self.vrf)
        entry.save()
        self.assertEqual(entry.poller.name, 'remote')
        self.assertEqual(entry.site, self.acquired)
        self.assertEqual(entry.tenant, self.tenant)
        twin = OnboardingRequest(address='10.10.1.5')
        twin.save()
        self.assertEqual((twin.poller.name, twin.site), ('main', self.hq))

    def test_the_job_handed_to_the_poller_carries_the_vrf(self):
        OnboardingRequest(address='10.10.1.5', vrf=self.vrf).save()
        OnboardingRequest(address='10.10.1.5').save()
        client = APIClient()
        client.force_authenticate(user=self.user)
        url = reverse('plugins-api:netbox_discovery-api:discoverypoller-check-in')
        remote = client.post(url, {'name': 'remote'}, format='json').data['jobs']
        main = client.post(url, {'name': 'main'}, format='json').data['jobs']
        self.assertEqual([(j['address'], j['vrf'], j['vrf_name']) for j in remote],
                         [('10.10.1.5', self.vrf.pk, 'ACQ')])
        self.assertEqual([(j['address'], j['vrf'], j['vrf_name']) for j in main],
                         [('10.10.1.5', None, '')])

    def test_the_request_page_says_which_table(self):
        entry = OnboardingRequest(address='10.10.1.5', vrf=self.vrf)
        entry.save()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(entry.get_absolute_url()), 'VRF <a')
        twin = OnboardingRequest(address='10.10.1.5')
        twin.save()
        self.assertContains(self.client.get(twin.get_absolute_url()), 'no VRF was chosen')

    def test_a_csv_import_names_the_vrf(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('plugins:netbox_discovery:onboardingrequest_bulk_import'),
            {'data': 'address,vrf\n10.10.1.7,ACQ\n10.10.1.8,\n', 'format': 'csv',
             'csv_delimiter': ','},
        )
        self.assertEqual(response.status_code, 302, response.content[:3000])
        by_address = {r.address: r for r in OnboardingRequest.objects.all()}
        self.assertEqual(by_address['10.10.1.7'].poller.name, 'remote')
        self.assertEqual(by_address['10.10.1.8'].poller.name, 'main')

    # --- what the poller relies on from NetBox itself

    def test_netbox_filters_addresses_to_the_global_table_with_vrf_id_null(self):
        """The scanner scopes every address lookup with ?vrf_id=<id>, or
        ?vrf_id=null for the global table. Pinned here because if NetBox
        ignored it, the scanner would be back to finding the other network's
        address and nothing on the poller would say so."""
        IPAddress.objects.create(address='10.10.1.5/24')
        IPAddress.objects.create(address='10.10.1.5/24', vrf=self.vrf)
        client = APIClient()
        client.force_authenticate(user=self.user)
        url = reverse('ipam-api:ipaddress-list')
        everywhere = client.get(url, {'address': '10.10.1.5/24'}).data['results']
        in_global = client.get(url, {'address': '10.10.1.5/24', 'vrf_id': 'null'}).data['results']
        in_vrf = client.get(url, {'address': '10.10.1.5/24', 'vrf_id': self.vrf.pk}).data['results']
        self.assertEqual(len(everywhere), 2)
        self.assertEqual([r['vrf'] for r in in_global], [None])
        self.assertEqual([r['vrf']['id'] for r in in_vrf], [self.vrf.pk])
        prefixes = client.get(reverse('ipam-api:prefix-list'),
                              {'contains': '10.10.1.5', 'vrf_id': 'null'}).data['results']
        self.assertEqual([p['vrf'] for p in prefixes], [None])
