"""Applying a discovery action across a selection.

A queue is worked in batches — one prefix appears and thirty addresses become
resolvable at once — so these are wanted across a selection far more often
than one at a time.

The case that matters is a mixed one. A real selection is a queue full of
things that stopped for different reasons, so refusing the lot because one of
them was already applied would make the button useless exactly when it is most
wanted.
"""

from dcim.models import Region, Site
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from extras.models import Tag
from ipam.models import Prefix

from netbox_discovery.choices import OnboardingStatusChoices as C
from netbox_discovery.models import OnboardingRequest

FINDINGS = {'devices': [{'name': 'sw', 'model': 'C9300-24P', 'serial': 'BULK1',
                         'manufacturer': 'Cisco', 'is_master': True,
                         'interfaces': [], 'modules': []}]}


@override_settings(PLUGINS_CONFIG={'netbox_discovery': {'default_region': 'bulk-us'}})
class BulkActionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.region = Region.objects.create(name='Bulk US', slug='bulk-us')
        cls.region.tags.add(Tag.objects.create(name='poller-bk', slug='poller-bk'))
        cls.site = Site.objects.create(name='Bulk Site', slug='bulk-site',
                                       region=cls.region)
        User = get_user_model()
        cls.user = User.objects.create_user('bulk', password='x')
        cls.user.is_superuser = True
        cls.user.save()

    def setUp(self):
        self.client.force_login(self.user)

    def stuck(self, last_octet, status=C.STATUS_REVIEW):
        entry = OnboardingRequest(address='198.51.100.%d' % last_octet)
        entry.save()
        entry.discovered = FINDINGS
        entry.status = status
        entry.save()
        return entry

    def add_prefix(self):
        Prefix.objects.create(prefix='198.51.100.0/24', scope=self.site)

    def post(self, name, entries):
        return self.client.post(
            reverse('plugins:netbox_discovery:%s' % name),
            {'pk': [e.pk for e in entries]}, follow=True,
        )

    def test_rechecking_a_selection_resolves_all_of_them(self):
        entries = [self.stuck(n) for n in (10, 11, 12)]
        self.add_prefix()
        self.post('onboardingrequest_bulk_recheck', entries)
        for entry in entries:
            entry.refresh_from_db()
            self.assertEqual(entry.status, C.STATUS_APPROVED, entry.address)
            self.assertEqual(entry.site, self.site)

    def test_a_mixed_selection_does_what_it_can(self):
        """The ordinary case, and the one that would be worth nothing if a
        single bad entry aborted the batch."""
        ok = [self.stuck(n) for n in (10, 11)]
        finished = self.stuck(12, status=C.STATUS_APPLIED)
        self.add_prefix()

        self.post('onboardingrequest_bulk_recheck', ok + [finished])

        for entry in ok:
            entry.refresh_from_db()
            self.assertEqual(entry.status, C.STATUS_APPROVED, entry.address)
        finished.refresh_from_db()
        self.assertEqual(finished.status, C.STATUS_APPLIED, 'a finished request moved')

    def test_what_was_left_alone_is_named_not_counted(self):
        """"3 were skipped" tells nobody anything they can act on."""
        finished = self.stuck(12, status=C.STATUS_APPLIED)
        self.add_prefix()
        response = self.post('onboardingrequest_bulk_recheck', [finished])
        text = ' '.join(m.message for m in response.context['messages'])
        self.assertIn('198.51.100.12', text)

    def test_selecting_nothing_says_so(self):
        response = self.post('onboardingrequest_bulk_recheck', [])
        text = ' '.join(m.message for m in response.context['messages'])
        self.assertIn('Nothing was selected', text)

    def test_bulk_retry_queues_them_for_a_fresh_scan(self):
        entries = [self.stuck(n) for n in (10, 11)]
        self.add_prefix()
        self.post('onboardingrequest_bulk_retry', entries)
        for entry in entries:
            entry.refresh_from_db()
            self.assertEqual(entry.status, C.STATUS_PENDING, entry.address)
            # Scan again discards the reading; that is the difference from
            # re-check, and it has to survive being done in bulk.
            self.assertFalse(entry.discovered, entry.address)

    def test_the_dropdown_is_on_the_list_page(self):
        self.stuck(10)
        response = self.client.get(
            reverse('plugins:netbox_discovery:onboardingrequest_list'))
        content = response.content.decode()
        self.assertIn('Discovery actions', content)
        for name in ('onboardingrequest_bulk_recheck', 'onboardingrequest_bulk_retry'):
            self.assertIn(reverse('plugins:netbox_discovery:%s' % name), content)

    def test_a_user_without_permission_changes_nothing(self):
        entry = self.stuck(10)
        self.add_prefix()
        User = get_user_model()
        weak = User.objects.create_user('weak', password='x')
        self.client.force_login(weak)
        self.post('onboardingrequest_bulk_recheck', [entry])
        entry.refresh_from_db()
        self.assertEqual(entry.status, C.STATUS_REVIEW)
