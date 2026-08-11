"""How a healthy poller reads on the pollers page.

The column was `is_stale`, and a boolean column draws false as a red cross —
so a poller doing exactly what it should showed a red ✗ under a heading that
said Stale. Technically correct and it reads as a fault, which is the only
thing a status column has to get right.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from netbox_discovery.models import DiscoveryPoller


class PollerFreshnessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user('poller-ui', password='x')
        cls.user.is_superuser = True
        cls.user.save()

    def setUp(self):
        self.client.force_login(self.user)

    def test_a_poller_that_just_checked_in_is_checking_in(self):
        poller = DiscoveryPoller.objects.create(name='fresh')
        poller.touch()
        self.assertTrue(poller.is_checking_in)
        self.assertFalse(poller.is_stale)

    def test_a_quiet_poller_is_not(self):
        poller = DiscoveryPoller.objects.create(name='quiet')
        poller.last_seen_at = timezone.now() - timedelta(days=1)
        poller.save()
        self.assertFalse(poller.is_checking_in)
        self.assertTrue(poller.is_stale)

    def test_one_that_never_checked_in_is_not_either(self):
        poller = DiscoveryPoller.objects.create(name='never')
        self.assertFalse(poller.is_checking_in)

    def test_the_two_are_always_opposites(self):
        for name, seen in (('a', timezone.now()),
                           ('b', timezone.now() - timedelta(days=1)),
                           ('c', None)):
            poller = DiscoveryPoller.objects.create(name=name, last_seen_at=seen)
            self.assertEqual(poller.is_checking_in, not poller.is_stale, name)

    def test_the_list_asks_the_positive_question(self):
        poller = DiscoveryPoller.objects.create(name='listed')
        poller.touch()
        content = self.client.get(
            reverse('plugins:netbox_discovery:discoverypoller_list')).content.decode()
        self.assertIn('Checking in', content)
        self.assertNotIn('>Stale<', content)

    def test_the_detail_page_says_the_healthy_case_out_loud(self):
        """Silence on a good poller leaves the page looking like it is missing
        something. It should say it is fine."""
        poller = DiscoveryPoller.objects.create(name='healthy')
        poller.touch()
        content = self.client.get(reverse(
            'plugins:netbox_discovery:discoverypoller',
            kwargs={'pk': poller.pk})).content.decode()
        self.assertIn('This poller is checking in', content)
        self.assertNotIn('has not checked in recently', content)

    def test_the_detail_page_still_warns_about_a_quiet_one(self):
        poller = DiscoveryPoller.objects.create(name='gone')
        poller.last_seen_at = timezone.now() - timedelta(days=1)
        poller.save()
        content = self.client.get(reverse(
            'plugins:netbox_discovery:discoverypoller',
            kwargs={'pk': poller.pk})).content.decode()
        self.assertIn('has not checked in recently', content)

    def test_the_api_still_reports_is_stale(self):
        """Left alone deliberately: "should I worry?" is the right question for
        a consumer, and renaming it would break anyone reading it."""
        poller = DiscoveryPoller.objects.create(name='api')
        poller.touch()
        response = self.client.get(
            reverse('plugins-api:netbox_discovery-api:discoverypoller-detail',
                    kwargs={'pk': poller.pk}))
        self.assertIn('is_stale', response.data)
        self.assertFalse(response.data['is_stale'])
