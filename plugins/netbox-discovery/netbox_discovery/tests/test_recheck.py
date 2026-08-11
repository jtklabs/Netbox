"""Re-check IPAM: the fix for a request that stopped because a prefix was missing.

The workflow this closes: an address falls outside every prefix, the default
region still supplies a poller so the device is scanned, and it stops for
review because there is nowhere to create it. Somebody creates the prefix —
and there was no way to say "look again" without discarding a scan that was
perfectly good and waiting for a poller to walk the device all over again.
"""

from dcim.models import Region, Site
from django.test import TestCase, override_settings
from extras.models import Tag
from ipam.models import Prefix

from netbox_discovery import actions
from netbox_discovery.choices import OnboardingStatusChoices as C
from netbox_discovery.models import OnboardingRequest

FINDINGS = {
    'sys_name': 'dal-sw-9',
    'devices': [{
        'name': 'dal-sw-9', 'model': 'C9300-24P', 'serial': 'RECHECK001',
        'manufacturer': 'Cisco', 'is_master': True,
        'interfaces': [], 'modules': [],
    }],
    'access_points': [],
}


# The situation only arises when an unplaceable address still gets a poller,
# which is what the default region is for. Pointed at the test region so the
# fallback fires here the way it does in a real deployment.
@override_settings(PLUGINS_CONFIG={'netbox_discovery': {'default_region': 'recheck-us'}})
class RecheckTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.region = Region.objects.create(name='Recheck US', slug='recheck-us')
        cls.region.tags.add(Tag.objects.create(name='poller-rc', slug='poller-rc'))
        cls.site = Site.objects.create(name='Recheck Site', slug='recheck-site',
                                       region=cls.region)

    def stuck_request(self):
        """A request that was scanned but has nowhere to go: no prefix."""
        entry = OnboardingRequest(address='198.51.100.10')
        entry.save()
        entry.discovered = FINDINGS
        entry.status = C.STATUS_REVIEW
        entry.error = 'No prefix placed this address'
        entry.save()
        return entry

    def add_the_missing_prefix(self):
        Prefix.objects.create(prefix='198.51.100.0/24', scope=self.site)

    def test_the_situation_this_exists_for(self):
        """Without a prefix the request has a poller but no site."""
        entry = self.stuck_request()
        self.assertIsNotNone(entry.poller)
        self.assertIsNone(entry.target_site)

    def test_creating_the_prefix_alone_changes_nothing(self):
        entry = self.stuck_request()
        self.add_the_missing_prefix()
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_REVIEW)
        self.assertIsNone(entry.target_site)

    def test_rechecking_finds_the_new_prefix(self):
        entry = self.stuck_request()
        self.add_the_missing_prefix()
        actions.recheck(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.site, self.site)

    def test_it_is_approved_so_the_poller_applies_it(self):
        """The only thing wrong was the missing site. Making somebody click
        Apply as well is ceremony, not a control."""
        entry = self.stuck_request()
        self.add_the_missing_prefix()
        actions.recheck(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_APPROVED)

    def test_the_scan_is_kept(self):
        """The whole point: the device answered fine, so re-walking it is
        pure delay."""
        entry = self.stuck_request()
        self.add_the_missing_prefix()
        actions.recheck(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.discovered, FINDINGS)

    def test_still_no_prefix_says_so_and_stays_in_review(self):
        entry = self.stuck_request()
        with self.assertRaises(actions.TransitionError):
            actions.recheck(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_REVIEW)
        self.assertTrue(entry.error)

    def test_something_else_wrong_keeps_it_in_review(self):
        """A site appearing must not wave through a reading that has another
        problem — here, no model, so no device type can be chosen."""
        entry = self.stuck_request()
        entry.discovered = {'devices': [{'name': 'x', 'model': '', 'serial': 'S'}]}
        entry.save()
        self.add_the_missing_prefix()
        with self.assertRaises(actions.TransitionError):
            actions.recheck(entry)
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_REVIEW)
        self.assertIn('model', entry.error)

    def test_a_request_with_no_scan_is_refused(self):
        """There is no reading to re-judge; Try again is the right lever."""
        entry = OnboardingRequest(address='198.51.100.11')
        entry.save()
        with self.assertRaises(actions.TransitionError):
            actions.recheck(entry)

    def test_a_finished_request_is_refused(self):
        entry = self.stuck_request()
        for status in C.TERMINAL:
            entry.status = status
            entry.save()
            with self.assertRaises(actions.TransitionError):
                actions.recheck(entry)
