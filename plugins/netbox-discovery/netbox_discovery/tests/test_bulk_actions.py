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


class TheDropdownSurvivesAQuickSearch(BulkActionTest):
    """htmx/table.html replaces .bulk-action-buttons wholesale on every HTMX
    response. Anything rendered inside that element is gone after the first
    quick search — which is exactly when somebody is narrowing a queue down to
    the rows they want to act on.
    """

    def list_html(self, **params):
        return self.client.get(
            reverse('plugins:netbox_discovery:onboardingrequest_list'),
            params).content.decode()

    def bulk_action_element(self, html):
        """The fragment NetBox's out-of-band swap will replace."""
        start = html.index('class="btn-list bulk-action-buttons"')
        return html[start:html.index('</div>', start)]

    def test_the_dropdown_is_not_inside_the_swapped_element(self):
        self.stuck(10)
        html = self.list_html()
        self.assertIn('Discovery actions', html)
        self.assertNotIn('Discovery actions', self.bulk_action_element(html),
                         'the dropdown is inside the element HTMX replaces')

    def test_a_quick_search_still_offers_it(self):
        self.stuck(10)
        html = self.list_html(q='198.51.100.10')
        self.assertIn('Discovery actions', html)

    def test_the_htmx_partial_does_not_carry_the_dropdown_away(self):
        """The partial should replace only NetBox's own buttons. If it carried
        a copy of the dropdown, the two would fight over the same element."""
        self.stuck(10)
        response = self.client.get(
            reverse('plugins:netbox_discovery:onboardingrequest_list'),
            {'q': '198.51.100.10'}, headers={'HX-Request': 'true'})
        body = response.content.decode()
        self.assertIn('hx-swap-oob', body, 'not an HTMX partial at all')
        self.assertNotIn('Discovery actions', body)

    def test_the_actions_still_work_on_a_filtered_selection(self):
        """The point of searching first is acting on what you found."""
        wanted = self.stuck(10)
        other = self.stuck(99)
        self.add_prefix()
        self.post('onboardingrequest_bulk_recheck', [wanted])
        wanted.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(wanted.status, C.STATUS_APPROVED)
        self.assertEqual(other.status, C.STATUS_REVIEW)


class ApplyingTheSwapTheWayHtmxDoes(BulkActionTest):
    """The structural tests above say the dropdown sits outside the swapped
    element. This performs the swap and checks what is actually left.

    htmx reads hx-swap-oob="outerHTML:.bulk-action-buttons" and replaces the
    first element matching that selector with the one carrying the attribute.
    Reproduced here rather than asserted about, because the bug was that the
    replacement quietly took the dropdown with it.
    """

    @staticmethod
    def element_span(html, needle):
        """(start, end) of the element containing `needle`, honouring nesting."""
        from html.parser import HTMLParser

        anchor = html.index(needle)
        start = html.rindex('<div', 0, anchor)

        class Walker(HTMLParser):
            depth = 0
            end = None

            def handle_starttag(self, tag, attrs):
                if tag == 'div':
                    self.depth += 1

            def handle_endtag(self, tag):
                if tag == 'div':
                    self.depth -= 1
                    if self.depth == 0 and self.end is None:
                        line, col = self.getpos()
                        self.end = col

        walker = Walker()
        rest = html[start:]
        walker.feed(rest)
        # Fall back to the raw close tag when the fragment is malformed.
        close = rest.rindex('</div>') + len('</div>')
        return start, start + close

    def test_the_dropdown_is_still_there_after_the_swap(self):
        self.stuck(10)
        url = reverse('plugins:netbox_discovery:onboardingrequest_list')
        page = self.client.get(url).content.decode()
        partial = self.client.get(
            url, {'q': '198.51.100.10'},
            headers={'HX-Request': 'true'}).content.decode()

        self.assertIn('Discovery actions', page)
        self.assertIn('hx-swap-oob="outerHTML:.bulk-action-buttons"', partial)

        # The replacement htmx would splice in...
        rep_start, rep_end = self.element_span(
            partial, 'hx-swap-oob="outerHTML:.bulk-action-buttons"')
        replacement = partial[rep_start:rep_end]
        # ...over the first matching element on the page.
        old_start, old_end = self.element_span(
            page, 'class="btn-list bulk-action-buttons"')
        swapped = page[:old_start] + replacement + page[old_end:]

        self.assertIn('Discovery actions', swapped,
                      'the out-of-band swap removed the dropdown')
        self.assertIn('bulk-action-buttons', swapped,
                      "NetBox's own buttons did not survive either")
