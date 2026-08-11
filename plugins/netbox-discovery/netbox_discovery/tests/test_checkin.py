"""Check-in hands a poller the work that was filed for it.

The failure this guards against is silent in the worst way: the poller
resolves its sites correctly, checks in successfully, is listed in the UI, and
is simply never given anything to do -- because it registered under a name one
`poller-` prefix away from the name its requests were filed under.
"""

from dcim.models import Region, Site
from django.contrib.auth import get_user_model
from django.urls import reverse
from extras.models import Tag
from ipam.models import Prefix
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from netbox_discovery.models import DiscoveryPoller, OnboardingRequest
from netbox_discovery.resolution import normalise_poller_name


class NormalisePollerNameTest(APITestCase):
    def test_the_tag_form_and_the_bare_form_are_the_same_poller(self):
        self.assertEqual(normalise_poller_name('poller-checkmk-us'), 'checkmk-us')
        self.assertEqual(normalise_poller_name('checkmk-us'), 'checkmk-us')

    def test_it_is_case_insensitive_about_the_prefix(self):
        self.assertEqual(normalise_poller_name('Poller-checkmk-us'), 'checkmk-us')

    def test_a_name_that_merely_contains_the_word_is_left_alone(self):
        self.assertEqual(normalise_poller_name('mypoller-eu'), 'mypoller-eu')

    def test_whitespace_is_trimmed(self):
        self.assertEqual(normalise_poller_name('  checkmk-us '), 'checkmk-us')


class CheckInTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.region = Region.objects.create(name='CI US', slug='ci-us')
        cls.site = Site.objects.create(name='CI Site', slug='ci-site', region=cls.region)
        cls.site.tags.add(Tag.objects.create(name='poller-ci', slug='poller-ci'))
        Prefix.objects.create(prefix='198.51.100.0/24', scope=cls.site)

        User = get_user_model()
        cls.user = User.objects.create_user('ci-poller', password='x')
        cls.user.is_superuser = True
        cls.user.save()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.request = OnboardingRequest(address='198.51.100.10')
        self.request.save()

    def _check_in(self, name):
        return self.client.post(
            reverse('plugins-api:netbox_discovery-api:discoverypoller-check-in'),
            {'name': name}, format='json',
        )

    def test_the_request_was_filed_under_the_bare_name(self):
        self.assertIsNotNone(self.request.poller)
        self.assertEqual(self.request.poller.name, 'ci')

    def test_checking_in_with_the_bare_name_gets_the_work(self):
        response = self._check_in('ci')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['jobs']), 1)
        self.assertEqual(response.data['jobs'][0]['address'], '198.51.100.10')

    def test_checking_in_with_the_full_tag_gets_the_same_work(self):
        """The regression: this used to register a second, empty poller."""
        response = self._check_in('poller-ci')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            len(response.data['jobs']), 1,
            'a poller configured with the whole tag was handed no work',
        )
        self.assertEqual(response.data['poller']['name'], 'ci')

    def test_it_does_not_create_a_second_poller_row(self):
        self._check_in('poller-ci')
        self.assertEqual(
            list(DiscoveryPoller.objects.values_list('name', flat=True)), ['ci'],
            'check-in created a duplicate poller under the prefixed name',
        )

    def test_another_pollers_work_is_not_handed_out(self):
        response = self._check_in('somewhere-else')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['jobs']), 0)
