"""The list of domains to take off reported hostnames.

Stripping happens on the pollers -- scripts/snmp-inventory/snmpinv/naming.py
and its tests. This is the NetBox half: keeping the list, storing an entry
the way the pollers compare it however it was typed, and handing it out.
"""

from dcim.models import Region, Site
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from extras.models import Tag
from ipam.models import Prefix
from rest_framework.test import APIClient, APITestCase

from netbox_discovery.models import OnboardingRequest, StrippedDomain


class StrippedDomainModelTest(TestCase):
    def test_an_entry_is_stored_the_way_it_is_compared(self):
        """The joining dot is inferred, never typed -- but people type it."""
        entry = StrippedDomain(domain=' .Google.COM. ')
        entry.full_clean()
        entry.save()
        self.assertEqual(entry.domain, 'google.com')
        self.assertEqual(str(entry), 'google.com')
        self.assertEqual(entry.example(), 'switch.google.com → switch')

    def test_the_same_domain_typed_differently_is_a_duplicate(self):
        StrippedDomain(domain='google.com').save()
        with self.assertRaises(ValidationError) as caught:
            StrippedDomain(domain='.GOOGLE.com').full_clean()
        self.assertIn('domain', caught.exception.message_dict)

    def test_nothing_and_nonsense_are_refused(self):
        for bad in ('', ' . ', 'google com', 'google..com'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    StrippedDomain(domain=bad).full_clean()

    def test_a_single_label_is_allowed(self):
        """'parts of the name': .local, .lan, a site code."""
        StrippedDomain(domain='local').full_clean()


class StrippedDomainUITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('domains-ui', password='x')
        cls.entry = StrippedDomain.objects.create(domain='google.com')

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_list_renders_with_the_effect_of_each_entry(self):
        response = self.client.get(reverse('plugins:netbox_discovery:strippeddomain_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'switch.google.com')

    def test_the_detail_page_explains_the_switch(self):
        response = self.client.get(self.entry.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sw1.floor2.google.com')
        self.assertContains(response, 'This is the only enabled domain.')

    def test_a_domain_can_be_added_from_the_form(self):
        response = self.client.post(
            reverse('plugins:netbox_discovery:strippeddomain_add'),
            {'domain': '.Corp.Example.com', 'enabled': 'on', 'description': '', 'comments': ''},
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        self.assertTrue(StrippedDomain.objects.filter(domain='corp.example.com').exists())

    def test_a_list_can_be_pasted_in(self):
        response = self.client.post(
            reverse('plugins:netbox_discovery:strippeddomain_bulk_import'),
            {'data': 'domain\nother.net\n.Third.org.\n', 'format': 'csv', 'csv_delimiter': ','},
        )
        self.assertEqual(response.status_code, 302, response.content[:3000])
        self.assertEqual(
            sorted(StrippedDomain.objects.values_list('domain', flat=True)),
            ['google.com', 'other.net', 'third.org'],
        )

    def test_the_detail_page_lists_scans_under_the_domain(self):
        region = Region.objects.create(name='SD US', slug='sd-us')
        site = Site.objects.create(name='SD Site', slug='sd-site', region=region)
        site.tags.add(Tag.objects.create(name='poller-sd', slug='poller-sd'))
        Prefix.objects.create(prefix='198.51.100.0/24', scope=site)
        for address, sys_name in (('198.51.100.10', 'test.Google.com'),
                                  ('198.51.100.11', 'other.example.net')):
            entry = OnboardingRequest(address=address)
            entry.save()
            entry.discovered = {'sys_name': sys_name, 'devices': [{'name': 'x', 'is_master': True}]}
            entry.save()
        response = self.client.get(self.entry.get_absolute_url())
        self.assertContains(response, 'test.Google.com')
        self.assertNotContains(response, 'other.example.net')


class StrippedDomainAPITest(APITestCase):
    """The poller's side of the conversation."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('domains-api', password='x')
        StrippedDomain.objects.create(domain='google.com')
        StrippedDomain.objects.create(domain='retired.example', enabled=False)
        cls.url = reverse('plugins-api:netbox_discovery-api:strippeddomain-list')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_a_poller_reads_only_the_enabled_domains(self):
        response = self.client.get(self.url, {'enabled': 'true'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['domain'] for r in response.data['results']], ['google.com'])

    def test_a_domain_created_over_the_api_is_normalised_too(self):
        """The serializer validates a throwaway instance and saves the raw
        values, so normalising only in clean() would store it as typed."""
        response = self.client.post(self.url, {'domain': '.Corp.Example.COM.'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(StrippedDomain.objects.filter(domain='corp.example.com').exists())
        self.assertEqual(response.data['domain'], 'corp.example.com')

    def test_a_duplicate_is_refused_over_the_api(self):
        response = self.client.post(self.url, {'domain': 'GOOGLE.com'}, format='json')
        self.assertEqual(response.status_code, 400)
