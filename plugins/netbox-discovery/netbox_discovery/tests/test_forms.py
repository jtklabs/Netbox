"""Form validation, driven the way the UI drives it.

These exist because of a bug that rendered perfectly and crashed on submit:
NetBox's CheckLastUpdatedMixin.clean() bare-returns when the instance has no
pk, and NetBoxModelForm passes that value straight through, so super().clean()
is None on every *add* and never on an edit. Anything that only rendered the
page, or only exercised the model, saw nothing wrong.

So every form here is bound to data and validated, not just constructed.
"""

from django.test import TestCase
from ipam.models import Prefix
from dcim.models import Region, Site
from extras.models import Tag

from netbox_discovery.forms import OnboardingRequestForm


class OnboardingRequestFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.region = Region.objects.create(name='Test US', slug='test-us')
        cls.site = Site.objects.create(name='Test Site', slug='test-site',
                                       region=cls.region)
        cls.tag = Tag.objects.create(name='poller-testpoller', slug='poller-testpoller')
        cls.site.tags.add(cls.tag)
        cls.prefix = Prefix.objects.create(prefix='198.51.100.0/24', scope=cls.site)

    def test_adding_a_resolvable_address_validates(self):
        """The plain case, and the one that used to raise AttributeError."""
        form = OnboardingRequestForm(data={'address': '198.51.100.10'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_an_unresolvable_address_is_a_field_error_not_a_crash(self):
        """The operator must get told what to fix, on the form, right now."""
        form = OnboardingRequestForm(data={'address': '203.0.113.99'})
        self.assertFalse(form.is_valid())
        self.assertTrue(
            form.errors.get('address') or form.errors.get('tenant'),
            f'expected a field error explaining the problem, got {form.errors!r}',
        )

    def test_a_blank_address_is_a_required_error(self):
        form = OnboardingRequestForm(data={'address': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('address', form.errors)

    def test_the_address_is_normalised_onto_cleaned_data(self):
        """A mask typed into the field must not reach the poller."""
        form = OnboardingRequestForm(data={'address': '198.51.100.10/24'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['address'], '198.51.100.10')

    def test_clean_survives_super_returning_none(self):
        """Pins the actual defect rather than only its symptom.

        Django permits clean() to return None — it means "cleaned_data is
        unchanged" — so our clean() must never assume a dict comes back.
        """
        form = OnboardingRequestForm(data={'address': '198.51.100.10'})
        form.is_valid()
        original = OnboardingRequestForm.__mro__[1].clean

        def returns_none(self):
            original(self)
            return None

        try:
            OnboardingRequestForm.__mro__[1].clean = returns_none
            again = OnboardingRequestForm(data={'address': '198.51.100.10'})
            self.assertTrue(again.is_valid(), again.errors)
        finally:
            OnboardingRequestForm.__mro__[1].clean = original
