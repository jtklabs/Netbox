from dcim.models import DeviceRole, Manufacturer, Platform, Site
from ipam.models import VRF
from tenancy.models import Tenant, TenantGroup
from django import forms
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm, NetBoxModelImportForm
from utilities.forms.fields import (
    CSVModelChoiceField,
    DynamicModelChoiceField,
    TagFilterField,
)
from utilities.forms.rendering import FieldSet

from netbox_discovery.choices import OnboardingStatusChoices
from netbox_discovery.models import DiscoveryIssue, DiscoveryPoller, OnboardingRequest
from netbox_discovery.resolution import resolve

__all__ = (
    'OnboardingRequestForm',
    'OnboardingRequestFilterForm',
    'OnboardingRequestImportForm',
    'OnboardingReviewForm',
    'OnboardingManualEntryForm',
    'DiscoveryPollerForm',
    'DiscoveryPollerFilterForm',
    'DiscoveryIssueForm',
)


class OnboardingRequestForm(NetBoxModelForm):
    """The onboarding form. One required field, on purpose.

    Everything else is either derived (site, poller) or read from the device
    (name, model, serial, version). The optional fields exist for the cases a
    person genuinely knows better than the device does — a naming standard the
    device has not been configured with yet, or a role that cannot be inferred
    from hardware at all.
    """

    tenant_group = DynamicModelChoiceField(
        queryset=TenantGroup.objects.all(), required=False, label='Tenant group',
        initial_params={'tenants': '$tenant'},
        help_text='Narrows the tenant list; not stored',
    )
    tenant = DynamicModelChoiceField(
        queryset=Tenant.objects.all(), required=False,
        query_params={'group_id': '$tenant_group'},
        help_text='Only needed when the address exists in more than one tenant',
    )
    vrf = DynamicModelChoiceField(
        queryset=VRF.objects.all(), required=False, label='VRF',
        help_text='Only needed when the address exists in more than one VRF',
    )
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
        # Second, not first: most addresses resolve without any of this, and
        # asking for a tenant up front would make the common case feel harder
        # than it is. The form says which to set when it actually needs one.
        FieldSet('tenant_group', 'tenant', 'vrf', name='Which network (if ambiguous)'),
        FieldSet('override_name', 'override_site', 'role', 'description',
                 name='Optional overrides'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = OnboardingRequest
        fields = ('address', 'tenant', 'vrf', 'override_name', 'override_site',
                  'role', 'description', 'tags')
        labels = {'override_name': 'Device name'}
        help_texts = {
            'address': 'The management IP. Everything else is discovered.',
            'override_name': "Leave blank to use the device's own hostname",
        }

    def clean(self):
        """Resolve the address while the user is still looking at the form.

        Deferring this to the poller would mean the request is accepted, sits
        in a queue, and quietly never runs — the operator finding out much
        later, with nothing to tell them the prefix was missing. Failing here
        costs them one correction now.

        The error lands on `tenant` rather than `address` when the address is
        fine but ambiguous, because the tenant field is the one to fill in.
        """
        # `or self.cleaned_data` because NetBox's CheckLastUpdatedMixin.clean()
        # bare-returns when the instance has no pk, and NetBoxModelForm passes
        # that straight through — so super().clean() is None on every *add*,
        # and only on adds. Django allows clean() to return None (it means
        # "cleaned_data is unchanged"), so the caller has to fall back to
        # self.cleaned_data rather than trusting the return value.
        cleaned = super().clean() or self.cleaned_data
        address = cleaned.get('address')
        if not address:
            return cleaned
        resolution = resolve(address, tenant=cleaned.get('tenant'),
                             vrf=cleaned.get('vrf'))
        if resolution.problem:
            field = 'tenant' if resolution.candidates else 'address'
            raise forms.ValidationError({field: resolution.problem})
        cleaned['address'] = resolution.address
        return cleaned


class OnboardingReviewForm(forms.Form):
    """Last chance to correct the derived site, role or name before applying."""

    override_name = forms.CharField(
        required=False, label='Device name',
        help_text="Blank uses the name the device reports for itself",
    )
    override_model = forms.CharField(
        required=False, label='Model',
        help_text='Only needed when the scan found no model. Everything else '
                  'the scan reported is kept.',
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
    tenant_id = DynamicModelChoiceField(
        queryset=Tenant.objects.all(), required=False, label='Tenant'
    )
    tag = TagFilterField(model)


class OnboardingRequestImportForm(NetBoxModelImportForm):
    """Bulk onboarding from a CSV of addresses.

    The same one-column shape as the scanner's own importer: an address is
    enough, because everything else is derived.
    """

    tenant = CSVModelChoiceField(
        queryset=Tenant.objects.all(), to_field_name='name', required=False,
        help_text='Needed only when the address is ambiguous across tenants',
    )
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
        fields = ('address', 'tenant', 'vrf', 'override_name', 'override_site',
                  'role', 'description')


class DiscoveryPollerForm(NetBoxModelForm):
    tenant = DynamicModelChoiceField(
        queryset=Tenant.objects.all(), required=False,
        help_text='Optional. Not how work is routed — a guard, so a request for '
                  'another tenant arriving here is flagged rather than scanned',
    )

    fieldsets = (
        FieldSet('name', 'tenant', 'description', name='Poller'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = DiscoveryPoller
        fields = ('name', 'tenant', 'description', 'comments', 'tags')
        help_texts = {
            'name': 'Must match the poller-&lt;name&gt; tag used on its sites and regions',
        }


class DiscoveryPollerFilterForm(NetBoxModelFilterSetForm):
    model = DiscoveryPoller
    tag = TagFilterField(model)


class OnboardingManualEntryForm(forms.Form):
    """Describe a device SNMP cannot tell us about.

    Plenty of gear has no SNMP, has it disabled, or sits behind something that
    will not pass it — and it still belongs in the inventory. This is the way
    in for those, using the same request, the same review and the same apply as
    a scanned device, so there is one path into DCIM rather than two.

    What is typed here is marked as entered by hand wherever it is shown. A
    hand-typed serial and an observed one are not equally trustworthy and
    should never look alike.
    """

    name = forms.CharField(
        label='Device name',
        help_text='What this device should be called in NetBox',
    )
    manufacturer = DynamicModelChoiceField(
        queryset=Manufacturer.objects.all(),
        help_text='Created if it does not exist yet',
    )
    model = forms.CharField(
        label='Model',
        help_text='Exactly as the vendor writes it, e.g. C9300-48P',
    )
    serial = forms.CharField(
        required=False,
        help_text='Strongly recommended — support contracts are matched on it',
    )
    platform = DynamicModelChoiceField(
        queryset=Platform.objects.all(), required=False,
        help_text='Operating system, if known',
    )
    role = DynamicModelChoiceField(
        queryset=DeviceRole.objects.all(), required=False,
        help_text="Blank uses the poller's default role",
    )
    override_site = DynamicModelChoiceField(
        queryset=Site.objects.all(), required=False, label='Site',
        help_text='Only needed if no prefix placed the address',
    )
    software_version = forms.CharField(
        required=False,
        help_text='Running version, if known',
    )


class DiscoveryIssueForm(NetBoxModelForm):
    """Only the fields a person settles. The observed ones stay as recorded."""

    class Meta:
        model = DiscoveryIssue
        fields = ('status', 'description', 'comments', 'tags')
        help_texts = {
            'status': 'Resolved once the duplicate is sorted out; Ignored if it '
                      'is expected and should stop being raised',
        }
