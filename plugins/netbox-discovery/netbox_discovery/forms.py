from dcim.models import DeviceRole, Site
from django import forms
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm, NetBoxModelImportForm
from utilities.forms.fields import (
    CSVModelChoiceField,
    DynamicModelChoiceField,
    TagFilterField,
)
from utilities.forms.rendering import FieldSet

from netbox_discovery.choices import OnboardingStatusChoices
from netbox_discovery.models import DiscoveryPoller, OnboardingRequest
from netbox_discovery.resolution import resolve

__all__ = (
    'OnboardingRequestForm',
    'OnboardingRequestFilterForm',
    'OnboardingRequestImportForm',
    'OnboardingReviewForm',
    'DiscoveryPollerForm',
    'DiscoveryPollerFilterForm',
)


class OnboardingRequestForm(NetBoxModelForm):
    """The onboarding form. One required field, on purpose.

    Everything else is either derived (site, poller) or read from the device
    (name, model, serial, version). The optional fields exist for the cases a
    person genuinely knows better than the device does — a naming standard the
    device has not been configured with yet, or a role that cannot be inferred
    from hardware at all.
    """

    override_site = DynamicModelChoiceField(
        queryset=Site.objects.all(), required=False, label='Site',
        help_text='Leave blank to use the site of the prefix containing the address',
    )
    role = DynamicModelChoiceField(
        queryset=DeviceRole.objects.all(), required=False,
        help_text="Leave blank to use the poller's default role",
    )

    fieldsets = (
        FieldSet('address', name='Device'),
        FieldSet('override_name', 'override_site', 'role', 'description',
                 name='Optional overrides'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = OnboardingRequest
        fields = ('address', 'override_name', 'override_site', 'role', 'description', 'tags')
        labels = {'override_name': 'Device name'}
        help_texts = {
            'address': 'The management IP. Everything else is discovered.',
            'override_name': "Leave blank to use the device's own hostname",
        }

    def clean_address(self):
        """Resolve the address while the user is still looking at the form.

        Deferring this to the poller would mean the request is accepted, sits
        in a queue, and quietly never runs — the operator finding out much
        later, with nothing to tell them the prefix was missing. Failing here
        costs them one correction now.
        """
        address = self.cleaned_data['address']
        resolution = resolve(address)
        if resolution.problem:
            raise forms.ValidationError(resolution.problem)
        return resolution.address


class OnboardingReviewForm(forms.Form):
    """Last chance to correct the derived site, role or name before applying."""

    override_name = forms.CharField(
        required=False, label='Device name',
        help_text="Blank uses the name the device reports for itself",
    )
    override_site = DynamicModelChoiceField(
        queryset=Site.objects.all(), required=False, label='Site',
        help_text='Blank uses the site derived from the prefix',
    )
    role = DynamicModelChoiceField(
        queryset=DeviceRole.objects.all(), required=False,
        help_text="Blank uses the poller's default role",
    )


class OnboardingRequestFilterForm(NetBoxModelFilterSetForm):
    model = OnboardingRequest

    status = forms.MultipleChoiceField(choices=OnboardingStatusChoices, required=False)
    poller_id = DynamicModelChoiceField(
        queryset=DiscoveryPoller.objects.all(), required=False, label='Poller'
    )
    site_id = DynamicModelChoiceField(
        queryset=Site.objects.all(), required=False, label='Site'
    )
    tag = TagFilterField(model)


class OnboardingRequestImportForm(NetBoxModelImportForm):
    """Bulk onboarding from a CSV of addresses.

    The same one-column shape as the scanner's own importer: an address is
    enough, because everything else is derived.
    """

    override_site = CSVModelChoiceField(
        queryset=Site.objects.all(), to_field_name='name', required=False,
        help_text='Optional; derived from the prefix when omitted',
    )
    role = CSVModelChoiceField(
        queryset=DeviceRole.objects.all(), to_field_name='name', required=False,
        help_text='Optional device role',
    )

    class Meta:
        model = OnboardingRequest
        fields = ('address', 'override_name', 'override_site', 'role', 'description')


class DiscoveryPollerForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('name', 'description', name='Poller'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = DiscoveryPoller
        fields = ('name', 'description', 'comments', 'tags')
        help_texts = {
            'name': 'Must match the poller-&lt;name&gt; tag used on its sites and regions',
        }


class DiscoveryPollerFilterForm(NetBoxModelFilterSetForm):
    model = DiscoveryPoller
    tag = TagFilterField(model)
