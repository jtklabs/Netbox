"""Retry, including the rescan case a request awaiting review needs.

The UI used to offer this only for failed and unresolved requests while
actions.retry() had always accepted review as well — the two front doors
disagreeing, which is the thing actions.py exists to prevent.
"""

from dcim.models import Region, Site
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from extras.models import Tag
from ipam.models import Prefix

from netbox_discovery import actions
from netbox_discovery.choices import OnboardingStatusChoices as C
from netbox_discovery.models import OnboardingRequest


class RetryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.region = Region.objects.create(name='Retry US', slug='retry-us')
        cls.site = Site.objects.create(name='Retry Site', slug='retry-site',
                                       region=cls.region)
        cls.site.tags.add(Tag.objects.create(name='poller-rt', slug='poller-rt'))
        Prefix.objects.create(prefix='198.51.100.0/24', scope=cls.site)

    def make(self, status, **kwargs):
        entry = OnboardingRequest(address='198.51.100.10')
        entry.save()
        for field, value in kwargs.items():
            setattr(entry, field, value)
        entry.status = status
        entry.save()
        return entry


class RetryFromReviewTest(RetryTestBase):
    FINDINGS = {'devices': [{'name': 'old-name', 'model': 'OLD-1'}]}

    def test_a_request_awaiting_review_can_be_scanned_again(self):
        entry = self.make(C.STATUS_REVIEW, discovered=self.FINDINGS)
        actions.retry(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_PENDING)

    def test_the_stale_findings_are_actually_discarded(self):
        """The card says they are. They have to be, or the detail page shows a
        reading derived against the IPAM the retry is correcting."""
        entry = self.make(C.STATUS_REVIEW, discovered=self.FINDINGS)
        actions.retry(entry)
        entry.refresh_from_db()
        self.assertFalse(entry.discovered)

    def test_hand_entered_details_are_kept(self):
        """Nobody types those expecting "try again" to delete them, and no
        rescan reproduces them — the device does not answer, that is why."""
        entry = self.make(C.STATUS_APPROVED, discovered=self.FINDINGS,
                          manually_entered=True)
        actions.retry(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.discovered, self.FINDINGS)

    def test_resolution_is_redone(self):
        entry = self.make(C.STATUS_REVIEW, discovered=self.FINDINGS)
        actions.retry(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.site, self.site)
        self.assertEqual(entry.poller.name, 'rt')

    def test_a_finished_request_is_still_refused(self):
        for status in C.TERMINAL:
            entry = self.make(status)
            with self.assertRaises(actions.TransitionError):
                actions.retry(entry)


class RetryButtonVisibilityTest(RetryTestBase):
    """What the detail page offers must match what the action accepts."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        User = get_user_model()
        cls.user = User.objects.create_user('retry-ui', password='x')
        cls.user.is_superuser = True
        cls.user.save()

    def setUp(self):
        self.client.force_login(self.user)

    def context_for(self, status):
        entry = self.make(status, discovered={'devices': []})
        url = reverse('plugins:netbox_discovery:onboardingrequest',
                      kwargs={'pk': entry.pk})
        return self.client.get(url).context

    def test_offered_while_awaiting_review(self):
        self.assertTrue(self.context_for(C.STATUS_REVIEW)['can_retry'])

    def test_worded_as_a_rescan_there(self):
        self.assertTrue(
            self.context_for(C.STATUS_REVIEW)['retry_discards_findings'])

    def test_still_offered_for_failed_and_unresolved(self):
        for status in (C.STATUS_FAILED, C.STATUS_UNRESOLVED):
            self.assertTrue(self.context_for(status)['can_retry'], status)

    def test_not_worded_as_a_rescan_for_those(self):
        """There is nothing to discard, so the warning would be a lie."""
        for status in (C.STATUS_FAILED, C.STATUS_UNRESOLVED):
            self.assertFalse(
                self.context_for(status)['retry_discards_findings'], status)

    def test_not_offered_once_finished(self):
        for status in C.TERMINAL:
            self.assertFalse(self.context_for(status)['can_retry'], status)
