from dcim.models import DeviceRole, Manufacturer, Platform, Site
from ipam.models import VRF
from tenancy.models import Tenant, TenantGroup
from django import forms
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm, NetBoxModelImportForm
from utilities.forms import BOOLEAN_WITH_BLANK_CHOICES
from utilities.forms.fields import (
    CSVModelChoiceField,
    DynamicModelChoiceField,
    TagFilterField,
)
from utilities.forms.rendering import FieldSet

from netbox_discovery.choices import (
    OnboardingStatusChoices,
    RuleMatchFieldChoices,
    RuleOperatorChoices,
    RuleSetFieldChoices,
)
from netbox_discovery.models import (
    DiscoveryIssue,
    DiscoveryPoller,
    DiscoveryRule,
    OnboardingRequest,
    StrippedDomain,
)
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
    'DiscoveryRuleForm',
    'DiscoveryRuleFilterForm',
    'StrippedDomainForm',
    'StrippedDomainFilterForm',
    'StrippedDomainImportForm',
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
        help_text='Optional. Inherited from the prefix, or from its VRF; set it '
                  'only to narrow the choice of prefix',
    )
    vrf = DynamicModelChoiceField(
        queryset=VRF.objects.all(), required=False, label='VRF',
        query_params={'tenant_id': '$tenant'},
        help_text='Which network the address is in. With a VRF, only the prefixes '
                  'in that VRF decide the site and the poller; left blank, only the '
                  'global table does. Set it for overlapping address space',
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
        FieldSet('vrf', 'tenant_group', 'tenant', name='Which network (blank is the global table)'),
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
            # Beside the thing to change: the VRF when the address lives in
            # another routing table, the tenant when that would break a tie.
            field = resolution.needs or ('tenant' if resolution.candidates else 'address')
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
        help_text='Optional; inherited from the prefix or its VRF',
    )
    vrf = CSVModelChoiceField(
        queryset=VRF.objects.all(), to_field_name='name', required=False,
        help_text='VRF name. Blank means the global table; set it for devices '
                  'in overlapping address space',
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

    Existing observations are prefilled and locked. Only missing details are
    entered here, and the request is marked as including manual entries.
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
    software_version = forms.CharField(
        required=False,
        help_text='Running version, if known',
    )

    def __init__(self, *args, entry, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.update({
            'name': entry.override_name or entry.address,
            'model': entry.override_model,
            'role': entry.role,
        })
        observed = dict(entry.primary_discovered)
        if not (observed.get('name') or '').strip():
            observed['name'] = (entry.discovered or {}).get('sys_name', '')
        for name in ('name', 'manufacturer', 'model', 'serial', 'platform',
                     'software_version'):
            value = observed.get(name)
            if not value or not str(value).strip():
                continue
            # The poller reports names, which may not exist in DCIM yet.
            # Display those directly instead of requiring a matching object.
            field = self.fields[name]
            if name in ('manufacturer', 'platform'):
                field = self.fields[name] = forms.CharField(
                    label=field.label, required=field.required,
                )
            field.disabled = True
            field.help_text = 'Discovered automatically'
            self.initial[name] = value


class DiscoveryIssueForm(NetBoxModelForm):
    """Only the fields a person settles. The observed ones stay as recorded."""

    class Meta:
        model = DiscoveryIssue
        fields = ('status', 'description', 'comments', 'tags')
        help_texts = {
            'status': 'Resolved once the duplicate is sorted out; Ignored if it '
                      'is expected and should stop being raised',
        }


class DiscoveryRuleForm(NetBoxModelForm):
    """One condition and one assignment, read top to bottom as a sentence.

    Deliberately not a general expression builder. A second condition is
    almost always "and the field is empty", which is the checkbox; anything
    more particular is a regular expression on the name or the sysDescr.
    """

    fieldsets = (
        FieldSet('name', 'enabled', 'weight', 'description', name='Rule'),
        FieldSet('match_field', 'match_operator', 'match_value', name='When'),
        FieldSet('set_field', 'set_value', 'only_if_blank', name='Then'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = DiscoveryRule
        fields = (
            'name', 'enabled', 'weight', 'match_field', 'match_operator',
            'match_value', 'set_field', 'set_value', 'only_if_blank',
            'description', 'comments', 'tags',
        )
        help_texts = {
            'name': 'Shown against every value this rule fills in, so make it '
                    'say what the rule is for',
        }


class DiscoveryRuleFilterForm(NetBoxModelFilterSetForm):
    model = DiscoveryRule

    enabled = forms.NullBooleanField(
        required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    match_field = forms.MultipleChoiceField(
        choices=RuleMatchFieldChoices, required=False, label='When this field',
    )
    match_operator = forms.MultipleChoiceField(
        choices=RuleOperatorChoices, required=False, label='Comparison',
    )
    set_field = forms.MultipleChoiceField(
        choices=RuleSetFieldChoices, required=False, label='Sets this field',
    )
    tag = TagFilterField(model)


class StrippedDomainForm(NetBoxModelForm):
    fieldsets = (
        FieldSet('domain', 'enabled', 'description', name='Domain to strip'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = StrippedDomain
        fields = ('domain', 'enabled', 'description', 'comments', 'tags')


class StrippedDomainFilterForm(NetBoxModelFilterSetForm):
    model = StrippedDomain

    enabled = forms.NullBooleanField(
        required=False, widget=forms.Select(choices=BOOLEAN_WITH_BLANK_CHOICES),
    )
    tag = TagFilterField(model)


class StrippedDomainImportForm(NetBoxModelImportForm):
    """A fleet's domains in one paste: one per line under a `domain` heading."""

    class Meta:
        model = StrippedDomain
        fields = ('domain', 'enabled', 'description')
