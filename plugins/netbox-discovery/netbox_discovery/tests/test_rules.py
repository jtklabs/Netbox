"""Discovery rules: what a device does not report, said once.

Rules are evaluated on the pollers — scripts/snmp-inventory/snmpinv/rules.py
and its tests. This is the NetBox half: storing them, refusing the ones a
poller could only skip, handing them out over the API, and showing on the
request page what they did.
"""

from dcim.models import Region, Site
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from extras.models import Tag
from ipam.models import Prefix
from rest_framework.test import APIClient, APITestCase

from netbox_discovery.choices import OnboardingStatusChoices as C
from netbox_discovery.models import DiscoveryRule, OnboardingRequest


def firepower_rule(**overrides):
    """The rule from the feature request: name contains X, model blank -> Y."""
    values = dict(name='Firepower by name', match_field='name', match_operator='contains',
                  match_value='fw-', set_field='model', set_value='FPR-2120')
    values.update(overrides)
    return DiscoveryRule(**values)


class DiscoveryRuleModelTest(TestCase):
    def test_the_example_from_the_request_reads_as_a_sentence(self):
        self.assertEqual(
            firepower_rule().sentence,
            'When the device name contains “fw-” and the model is empty, '
            'set the model to “FPR-2120”.',
        )

    def test_replacing_is_said_out_loud(self):
        self.assertIn('replacing whatever the device reports',
                      firepower_rule(only_if_blank=False).sentence)

    def test_a_regex_that_will_not_compile_is_refused(self):
        """Refused here, where somebody is looking, rather than skipped on a
        poller where nobody is."""
        with self.assertRaises(ValidationError) as caught:
            firepower_rule(match_operator='regex', match_value='(').full_clean()
        self.assertIn('match_value', caught.exception.message_dict)

    def test_a_regex_that_compiles_is_fine(self):
        firepower_rule(match_operator='regex', match_value=r'^fw-\d+').full_clean()

    def test_whitespace_is_not_a_value(self):
        with self.assertRaises(ValidationError) as caught:
            firepower_rule(set_value='   ').full_clean()
        self.assertIn('set_value', caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            firepower_rule(match_value='   ').full_clean()
        self.assertIn('match_value', caught.exception.message_dict)

    def test_rules_order_by_weight_then_name(self):
        """The order the poller applies them in."""
        firepower_rule(name='zeta', weight=100).save()
        firepower_rule(name='alpha', weight=100).save()
        firepower_rule(name='first', weight=10).save()
        self.assertEqual(list(DiscoveryRule.objects.values_list('name', flat=True)),
                         ['first', 'alpha', 'zeta'])


class DiscoveryRuleUITest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('rules-ui', password='x')
        cls.rule = firepower_rule()
        cls.rule.save()

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_list_shows_each_rule_as_a_sentence(self):
        response = self.client.get(reverse('plugins:netbox_discovery:discoveryrule_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'When the device name contains')

    def test_the_detail_page_renders(self):
        response = self.client.get(self.rule.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'FPR-2120')
        self.assertContains(response, 'No onboarding scan has matched this rule yet')

    def test_the_add_form_renders(self):
        response = self.client.get(reverse('plugins:netbox_discovery:discoveryrule_add'))
        self.assertEqual(response.status_code, 200)

    def _form(self, **overrides):
        data = {
            'name': 'Palo Alto by sysDescr', 'enabled': 'on', 'weight': 50,
            'match_field': 'sys_descr', 'match_operator': 'contains',
            'match_value': 'palo alto', 'set_field': 'platform', 'set_value': 'PAN-OS',
            'only_if_blank': 'on', 'description': '', 'comments': '',
        }
        data.update(overrides)
        return data

    def test_a_rule_can_be_created_from_the_form(self):
        response = self.client.post(reverse('plugins:netbox_discovery:discoveryrule_add'),
                                    self._form())
        self.assertEqual(response.status_code, 302, response.content[:3000])
        created = DiscoveryRule.objects.get(name='Palo Alto by sysDescr')
        self.assertEqual(created.weight, 50)
        self.assertTrue(created.only_if_blank)

    def test_the_form_refuses_a_bad_regex(self):
        response = self.client.post(
            reverse('plugins:netbox_discovery:discoveryrule_add'),
            self._form(name='Bad', match_operator='regex', match_value='('),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DiscoveryRule.objects.filter(name='Bad').exists())

    def test_the_filter_by_what_a_rule_sets(self):
        firepower_rule(name='os', set_field='platform', set_value='FTD').save()
        response = self.client.get(reverse('plugins:netbox_discovery:discoveryrule_list'),
                                   {'set_field': 'platform'})
        self.assertContains(response, 'FTD')
        self.assertNotContains(response, 'FPR-2120')


class DiscoveryRuleAPITest(APITestCase):
    """The poller's side of the conversation."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('rules-api', password='x')
        firepower_rule(name='on').save()
        firepower_rule(name='off', enabled=False).save()
        cls.url = reverse('plugins-api:netbox_discovery-api:discoveryrule-list')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_a_poller_reads_only_the_enabled_rules(self):
        response = self.client.get(self.url, {'enabled': 'true'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['name'] for r in response.data['results']], ['on'])

    def test_choices_come_out_the_way_the_poller_unwraps_them(self):
        """rules.py's rule_from_api accepts a choice either as its bare value
        or as NetBox's {value, label} form; pin that one of the two is what
        this NetBox sends, so a change in rendering fails here and not on a
        poller."""
        item = self.client.get(self.url, {'enabled': 'true'}).data['results'][0]

        def value(choice):
            return choice['value'] if isinstance(choice, dict) else choice

        self.assertEqual(value(item['match_field']), 'name')
        self.assertEqual(value(item['match_operator']), 'contains')
        self.assertEqual(value(item['set_field']), 'model')
        self.assertEqual(item['match_value'], 'fw-')
        self.assertEqual(item['set_value'], 'FPR-2120')
        self.assertTrue(item['only_if_blank'])
        self.assertEqual(item['weight'], 100)
        self.assertIn('set the model to', item['sentence'])

    def test_a_rule_can_be_created_over_the_api(self):
        response = self.client.post(self.url, {
            'name': 'Palo Alto by sysDescr', 'match_field': 'sys_descr',
            'match_operator': 'contains', 'match_value': 'palo alto',
            'set_field': 'platform', 'set_value': 'PAN-OS',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(DiscoveryRule.objects.get(name='Palo Alto by sysDescr').only_if_blank)

    def test_a_bad_regex_is_refused_over_the_api_too(self):
        response = self.client.post(self.url, {
            'name': 'bad', 'match_field': 'name', 'match_operator': 'regex',
            'match_value': '(', 'set_field': 'model', 'set_value': 'X',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('match_value', response.data)


class ScanReportsWhatRulesSuppliedTest(APITestCase):
    """A poller reports the values rules filled in alongside the reading."""

    @classmethod
    def setUpTestData(cls):
        region = Region.objects.create(name='Rules US', slug='rules-us')
        cls.site = Site.objects.create(name='Rules Site', slug='rules-site', region=region)
        cls.site.tags.add(Tag.objects.create(name='poller-rules', slug='poller-rules'))
        Prefix.objects.create(prefix='198.51.100.0/24', scope=cls.site)
        cls.user = get_user_model().objects.create_superuser('rules-scan', password='x')
        cls.rule = firepower_rule()
        cls.rule.save()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.request = OnboardingRequest(address='198.51.100.10')
        self.request.save()

    APPLIED = [{'rule': 'Firepower by name', 'device': 'fw-dal-01',
                'field': 'model', 'value': 'FPR-2120', 'previous': ''}]

    def _report(self, **extra):
        payload = {
            'ok': True, 'sys_name': 'fw-dal-01',
            'sys_descr': 'Cisco Firepower Threat Defense',
            'devices': [{'name': 'fw-dal-01', 'model': 'FPR-2120', 'serial': 'JAD12345678',
                         'manufacturer': 'Cisco', 'is_master': True}],
        }
        payload.update(extra)
        return self.client.post(
            reverse('plugins-api:netbox_discovery-api:onboardingrequest-scanned',
                    kwargs={'pk': self.request.pk}),
            payload, format='json',
        )

    def test_the_request_keeps_what_the_rules_supplied(self):
        response = self._report(rules_applied=self.APPLIED)
        self.assertEqual(response.status_code, 200, response.data)
        self.request.refresh_from_db()
        self.assertEqual(self.request.rules_applied, self.APPLIED)

    def test_a_model_a_rule_supplied_is_a_model(self):
        """The point of the feature: the scan is not held for 'no model'."""
        self._report(rules_applied=self.APPLIED)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, C.STATUS_APPROVED)

    def test_an_older_poller_that_sends_none_is_fine(self):
        response = self._report()
        self.assertEqual(response.status_code, 200, response.data)
        self.request.refresh_from_db()
        self.assertEqual(self.request.rules_applied, [])

    def test_the_request_page_names_the_rule_and_links_to_it(self):
        self._report(rules_applied=self.APPLIED)
        web = Client()
        web.force_login(self.user)
        response = web.get(self.request.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Filled in by rules')
        self.assertContains(response, self.rule.get_absolute_url())

    def test_a_rule_since_deleted_still_shows_under_its_recorded_name(self):
        self._report(rules_applied=self.APPLIED)
        self.rule.delete()
        web = Client()
        web.force_login(self.user)
        response = web.get(self.request.get_absolute_url())
        self.assertContains(response, 'Firepower by name')
        self.assertContains(response, 'no longer exists under this name')

    def test_the_rule_page_lists_the_scans_it_touched(self):
        self._report(rules_applied=self.APPLIED)
        web = Client()
        web.force_login(self.user)
        response = web.get(self.rule.get_absolute_url())
        self.assertContains(response, self.request.address)
