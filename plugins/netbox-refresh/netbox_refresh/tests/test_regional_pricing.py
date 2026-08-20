"""Per-site pricing and the report's regional cost breakdown.

The same box does not cost the same in every country. What matters here is
precedence — a site price beats the nearest region's, which beats a farther
ancestor's, which beats the baseline — and that the report sums money at each
unit's own resolved price, per currency, never converting.
"""

from datetime import date
from decimal import Decimal

from dcim.models import (
    Device, DeviceRole, DeviceType, Manufacturer, Region, Site,
)
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from netbox_refresh.models import ModelLifecycle, ReplacementPrice
from netbox_refresh.pricing import PriceResolver


class RegionalPricingTestData(TestCase):
    @classmethod
    def setUpTestData(cls):
        # emea -> uk -> (london); emea -> (paris); amer -> (dallas);
        # (remote) has no region at all.
        cls.emea = Region.objects.create(name='EMEA', slug='emea')
        cls.uk = Region.objects.create(name='UK', slug='uk', parent=cls.emea)
        cls.amer = Region.objects.create(name='AMER', slug='amer')
        cls.london = Site.objects.create(name='London', slug='london', region=cls.uk)
        cls.paris = Site.objects.create(name='Paris', slug='paris', region=cls.emea)
        cls.dallas = Site.objects.create(name='Dallas', slug='dallas', region=cls.amer)
        cls.remote = Site.objects.create(name='Remote', slug='remote')

        cls.mfr = Manufacturer.objects.create(name='Cisco', slug='cisco')
        cls.dt = DeviceType.objects.create(manufacturer=cls.mfr, model='C9300-24P',
                                           slug='c9300-24p')
        cls.role = DeviceRole.objects.create(name='Access', slug='access')
        for name, site in (('lon1', cls.london), ('par1', cls.paris),
                           ('dal1', cls.dallas), ('rem1', cls.remote)):
            Device.objects.create(name=name, device_type=cls.dt,
                                  role=cls.role, site=site)

        cls.lifecycle = ModelLifecycle(
            assigned_object=cls.dt, end_of_support=date(2027, 1, 1),
            replacement_cost=Decimal('1000.00'), currency='USD',
        )
        cls.lifecycle.save()


class PriceResolverTest(RegionalPricingTestData):
    def resolve(self, site):
        return PriceResolver([self.lifecycle.pk]).resolve(self.lifecycle, site)

    def test_no_prices_means_the_baseline(self):
        hit = self.resolve(self.dallas)
        self.assertEqual((hit.cost, hit.currency, hit.source),
                         (Decimal('1000.00'), 'USD', 'base'))

    def test_a_region_price_covers_sites_nested_below_it(self):
        """A price on EMEA covers London even though London's own region is UK."""
        ReplacementPrice.objects.create(lifecycle=self.lifecycle, region=self.emea,
                                        cost=Decimal('1200.00'), currency='EUR')
        hit = self.resolve(self.london)
        self.assertEqual((hit.cost, hit.currency, hit.source),
                         (Decimal('1200.00'), 'EUR', 'region'))

    def test_the_nearest_region_wins_over_an_ancestor(self):
        ReplacementPrice.objects.create(lifecycle=self.lifecycle, region=self.emea,
                                        cost=Decimal('1200.00'), currency='EUR')
        ReplacementPrice.objects.create(lifecycle=self.lifecycle, region=self.uk,
                                        cost=Decimal('1100.00'), currency='GBP')
        self.assertEqual(self.resolve(self.london).cost, Decimal('1100.00'))
        # Paris hangs off EMEA directly; the UK price must not leak onto it.
        self.assertEqual(self.resolve(self.paris).cost, Decimal('1200.00'))

    def test_a_site_price_beats_every_region(self):
        ReplacementPrice.objects.create(lifecycle=self.lifecycle, region=self.uk,
                                        cost=Decimal('1100.00'), currency='GBP')
        ReplacementPrice.objects.create(lifecycle=self.lifecycle, site=self.london,
                                        cost=Decimal('1500.00'), currency='GBP')
        hit = self.resolve(self.london)
        self.assertEqual((hit.cost, hit.source), (Decimal('1500.00'), 'site'))

    def test_a_region_price_does_not_reach_other_trees(self):
        ReplacementPrice.objects.create(lifecycle=self.lifecycle, region=self.emea,
                                        cost=Decimal('1200.00'), currency='EUR')
        self.assertEqual(self.resolve(self.dallas).source, 'base')

    def test_a_regionless_site_gets_the_baseline(self):
        ReplacementPrice.objects.create(lifecycle=self.lifecycle, region=self.emea,
                                        cost=Decimal('1200.00'), currency='EUR')
        self.assertEqual(self.resolve(self.remote).source, 'base')

    def test_no_price_anywhere_is_none_not_zero(self):
        """A missing price must be visible, not silently priced at nothing."""
        self.lifecycle.replacement_cost = None
        self.lifecycle.save()
        self.assertIsNone(self.resolve(self.dallas))

    def test_exactly_one_scope_is_enforced(self):
        from django.core.exceptions import ValidationError
        both = ReplacementPrice(lifecycle=self.lifecycle, region=self.emea,
                                site=self.london, cost=Decimal('1'))
        with self.assertRaises(ValidationError):
            both.full_clean()
        neither = ReplacementPrice(lifecycle=self.lifecycle, cost=Decimal('1'))
        with self.assertRaises(ValidationError):
            neither.full_clean()


class RegionBreakdownReportTest(RegionalPricingTestData):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ReplacementPrice.objects.create(lifecycle=cls.lifecycle, region=cls.emea,
                                        cost=Decimal('1200.00'), currency='EUR')
        ReplacementPrice.objects.create(lifecycle=cls.lifecycle, site=cls.london,
                                        cost=Decimal('1500.00'), currency='GBP')
        User = get_user_model()
        cls.user = User.objects.create_user('pricing', password='x')
        cls.user.is_superuser = True
        cls.user.save()

    def setUp(self):
        self.client.force_login(self.user)

    def report(self, **params):
        return self.client.get(reverse('plugins:netbox_refresh:refresh_report'), params)

    def test_totals_sum_each_unit_at_its_own_sites_price(self):
        """London 1500 GBP, Paris 1200 EUR, Dallas + regionless 1000 USD each."""
        totals = self.report().context['totals']
        self.assertEqual(totals, {
            'GBP': Decimal('1500.00'),
            'EUR': Decimal('1200.00'),
            'USD': Decimal('2000.00'),
        })

    def test_the_breakdown_has_one_row_per_region(self):
        rows = {r['region']: r for r in self.report().context['region_table'].data}
        self.assertEqual(rows['EMEA / UK']['total'], '1,500.00 GBP')
        self.assertEqual(rows['EMEA']['total'], '1,200.00 EUR')
        self.assertEqual(rows['AMER']['total'], '1,000.00 USD')
        self.assertEqual(rows['(no region)']['total'], '1,000.00 USD')

    def test_region_labels_carry_their_tree_path(self):
        labels = [r['region'] for r in self.report().context['region_table'].data]
        self.assertIn('EMEA / UK', labels)

    def test_the_regionless_bucket_sorts_last(self):
        labels = [r['region'] for r in self.report().context['region_table'].data]
        self.assertEqual(labels[-1], '(no region)')

    def test_a_units_missing_price_is_reported_not_zeroed(self):
        self.lifecycle.replacement_cost = None
        self.lifecycle.save()
        context = self.report().context
        # Dallas and the regionless site now have no price at all.
        self.assertEqual(context['unpriced_units'], 2)
        self.assertEqual(context['missing_cost'], 1)
        self.assertNotIn('USD', context['totals'])

    def test_the_model_row_says_the_price_varies(self):
        rows = self.report().context['table'].data
        self.assertEqual(len(rows), 1)
        self.assertIn('(+2 regional)', rows[0]['unit_cost'])
        self.assertIn('GBP', rows[0]['extended_cost'])
        self.assertIn('EUR', rows[0]['extended_cost'])
        self.assertIn('USD', rows[0]['extended_cost'])

    def test_site_filter_narrows_the_breakdown_too(self):
        context = self.report(site=self.london.pk).context
        self.assertEqual(context['totals'], {'GBP': Decimal('1500.00')})
        labels = [r['region'] for r in context['region_table'].data]
        self.assertEqual(labels, ['EMEA / UK'])
