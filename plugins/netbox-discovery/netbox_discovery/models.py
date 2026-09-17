import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from netbox.models import PrimaryModel

from netbox_discovery.choices import (
    IssueKindChoices,
    IssueStatusChoices,
    OnboardingStatusChoices,
    ReplacementKindChoices,
    RuleMatchFieldChoices,
    RuleOperatorChoices,
    RuleSetFieldChoices,
)
from netbox_discovery.utils import plugin_setting

__all__ = (
    'DiscoveryIssue',
    'DiscoveryPoller',
    'DiscoveryRule',
    'StrippedDomain',
    'HardwareReplacement',
    'OnboardingRequest',
    'UpgradeJob',
    'UpgradeGroup',
    'UpgradeDependency',
    'CommandOutput',
)


class DiscoveryPoller(PrimaryModel):
    """A remote SNMP poller, and when it was last heard from.

    The poller's *workload* is not stored here — that is decided by
    `poller-<name>` tags on sites and regions, and duplicating it would give
    two answers that could disagree. What is stored is the thing tags cannot
    express: whether the box is still alive.

    That matters because the whole flow is pull-based. A request sits in the
    queue until its poller wakes up, so "nothing has happened yet" is the
    normal state for a while, and the only way to tell it apart from "the
    poller is dead" is the last check-in.

    Rows are created by the pollers themselves on first check-in. Nobody has to
    register a poller by hand before it can work.
    """

    name = models.CharField(
        max_length=100, unique=True,
        help_text='Matches the poller-&lt;name&gt; tag on the sites it owns',
    )
    last_seen_at = models.DateTimeField(
        blank=True, null=True, help_text='When this poller last checked in'
    )
    upgrade_last_seen_at = models.DateTimeField(
        blank=True, null=True, editable=False,
        help_text='Last upgrade-worker check-in, heartbeat or accepted progress report; excludes SNMP-only activity',
    )
    version = models.CharField(
        max_length=50, blank=True, help_text='Scanner version reported at check-in'
    )
    last_scan_summary = models.CharField(
        max_length=200, blank=True,
        help_text='What the poller reported doing at its last check-in',
    )
    # Not how work is routed — that follows from the prefix's site — but a
    # useful guard: a request for another tenant arriving at this poller almost
    # certainly means a site is tagged for the wrong one.
    tenant = models.ForeignKey(
        to='tenancy.Tenant', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='+', help_text='Whose network this poller sits in, if only one',
    )

    clone_fields = ()

    class Meta:
        ordering = ('name',)
        verbose_name = 'poller'
        verbose_name_plural = 'pollers'

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:discoverypoller', args=[self.pk])

    @property
    def tag_slug(self):
        return '%s%s' % (plugin_setting('poller_tag_prefix'), self.name)

    @property
    def seconds_since_seen(self):
        if self.last_seen_at is None:
            return None
        return int((timezone.now() - self.last_seen_at).total_seconds())

    @property
    def is_stale(self):
        """Has this poller been quiet long enough to worry about?

        A poller that has never checked in is stale rather than unknown: it was
        created by a check-in, so a null here means something odd happened.
        """
        if self.last_seen_at is None:
            return True
        threshold = timedelta(minutes=plugin_setting('poller_stale_after_minutes'))
        return timezone.now() - self.last_seen_at > threshold

    @property
    def is_checking_in(self):
        """The healthy state, stated positively.

        is_stale is the right question for the code — everything that acts on
        it is asking "should I worry?" — and the wrong one for a column. A
        boolean column renders false as a red cross, so a poller doing exactly
        what it should showed a red cross under a heading that said Stale, and
        read as a fault. The list asks this instead, so green means well.
        """
        return not self.is_stale

    def get_status_color(self):
        return 'red' if self.is_stale else 'green'

    @property
    def sites(self):
        from netbox_discovery.resolution import sites_for_poller

        return sites_for_poller(self.name)

    def touch(self, version='', summary='', upgrade=False):
        """Record a check-in without writing a changelog entry.

        Deliberately a queryset update. A poller checking in every few minutes
        would otherwise write an ObjectChange per poller per interval and bury
        every change that matters under a wall of heartbeats.
        """
        now = timezone.now()
        fields = {'last_seen_at': now}
        if upgrade:
            fields['upgrade_last_seen_at'] = now
        if version:
            fields['version'] = version
        if summary:
            fields['last_scan_summary'] = summary
        DiscoveryPoller.objects.filter(pk=self.pk).update(**fields)
        for key, value in fields.items():
            setattr(self, key, value)


class OnboardingRequest(PrimaryModel):
    """One "please add the device at this address" request, and its outcome.

    The only thing a person supplies is the address. Site, poller, model,
    serial and everything else is either derived from IPAM or read from the
    device, because those are the parts people get wrong when asked to type
    them.

    Resolution happens at save time rather than when a poller picks the request
    up. Failing immediately — with a message saying which prefix is missing or
    which site needs a tag — is far more use than accepting the request and
    having it sit in a queue that nothing will ever service.
    """

    address = models.CharField(
        max_length=64, verbose_name='IP address',
        help_text='Management address of the device to onboard',
    )
    status = models.CharField(
        max_length=30, choices=OnboardingStatusChoices,
        default=OnboardingStatusChoices.STATUS_PENDING,
    )

    # --- Resolved from IPAM at save time.
    prefix = models.ForeignKey(
        to='ipam.Prefix', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='+', help_text='The prefix that placed this address',
    )
    site = models.ForeignKey(
        to='dcim.Site', on_delete=models.PROTECT, blank=True, null=True,
        related_name='+', help_text='Derived from the containing prefix',
    )
    poller = models.ForeignKey(
        to='netbox_discovery.DiscoveryPoller', on_delete=models.SET_NULL,
        blank=True, null=True, related_name='requests',
    )
    # Part of the key, not decoration. Address space overlaps across companies
    # we have bought, so the same address can sit in two prefixes and only the
    # tenant says which device is meant.
    tenant = models.ForeignKey(
        to='tenancy.Tenant', on_delete=models.PROTECT, blank=True, null=True,
        related_name='+',
        help_text='Required only when the address is ambiguous across tenants',
    )
    vrf = models.ForeignKey(
        to='ipam.VRF', on_delete=models.PROTECT, blank=True, null=True,
        related_name='+', verbose_name='VRF',
        help_text='The routing table the address is in. Only prefixes in this VRF '
                  'place it; blank means only the global table does. The poller '
                  'writes the device\'s addresses into the same VRF.',
    )
    manually_entered = models.BooleanField(
        default=False,
        help_text='The hardware details were typed in rather than observed, '
                  'because the device could not be scanned',
    )
    used_default_region = models.BooleanField(
        default=False,
        help_text='No prefix matched; the poller came from the default region '
                  'and a site must be chosen before this can be applied',
    )

    # --- What the operator may override before approving. Everything else the
    # scan reports is taken as read; these three are the ones a human is
    # sometimes better placed to know than the device is.
    override_model = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='Model override',
        help_text='Used when the device reports no model of its own. Some '
                  'platforms publish none at all — a Firepower 2120 among '
                  'them — and without one there is no device type to create.',
    )
    override_name = models.CharField(
        max_length=64, blank=True,
        help_text="Use this name instead of the device's own hostname",
    )
    override_site = models.ForeignKey(
        to='dcim.Site', on_delete=models.PROTECT, blank=True, null=True,
        related_name='+', help_text='Place the device here instead of the derived site',
    )
    role = models.ForeignKey(
        to='dcim.DeviceRole', on_delete=models.PROTECT, blank=True, null=True,
        related_name='+', help_text="Role for the new device; the poller's default if unset",
    )

    # --- Filled in by the poller.
    discovered = models.JSONField(
        blank=True, default=dict,
        help_text='What the scan found, as reported by the poller',
    )
    error = models.TextField(blank=True)
    device = models.ForeignKey(
        to='dcim.Device', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='onboarding_requests',
        help_text='The device this request created',
    )

    requested_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        blank=True, null=True, related_name='+',
    )
    claimed_at = models.DateTimeField(blank=True, null=True)
    scanned_at = models.DateTimeField(blank=True, null=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)
    reviewed_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        blank=True, null=True, related_name='+',
    )
    applied_at = models.DateTimeField(blank=True, null=True)

    clone_fields = ('role',)

    class Meta:
        ordering = ('-created',)
        verbose_name = 'onboarding request'
        verbose_name_plural = 'onboarding requests'
        indexes = (
            models.Index(fields=('status',)),
            models.Index(fields=('address',)),
        )

    def __str__(self):
        return self.address

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:onboardingrequest', args=[self.pk])

    def get_status_color(self):
        return OnboardingStatusChoices.colors.get(self.status)

    # ------------------------------------------------------------------ #

    def resolve_target(self, raise_on_failure=False):
        """Work out the site and poller for this address, from IPAM.

        Lives here rather than only in `clean()` because `clean()` is a form
        concept: the REST API, bulk import and anything using the ORM directly
        never call it, and a request created through any of those would have
        arrived with no site and no poller and sat in the queue forever.
        `save()` calls this, so every path gets the same answer.
        """
        from netbox_discovery.resolution import resolve

        if not self.address:
            if raise_on_failure:
                raise ValidationError({'address': 'Enter an IP address.'})
            return

        resolution = resolve(self.address, tenant=self.tenant, vrf=self.vrf)
        self.address = resolution.address
        # Partial results are kept, so the detail page can show how far the
        # chain got before it broke rather than just saying "no".
        self.prefix = resolution.prefix
        self.used_default_region = resolution.used_default_region
        if resolution.site is not None:
            self.site = resolution.site
        # A prefix that carries a tenant tells us the owner even when the
        # person did not; inheriting it means the created device is filed
        # against the right company without anyone typing it.
        if self.tenant is None and resolution.tenant is not None:
            self.tenant = resolution.tenant

        if not resolution.ok:
            # A form submission is rejected outright so the person fixes it
            # while they are still looking at it. Anything else is recorded as
            # unresolved with the reason attached — refusing the save would
            # make an existing request uneditable if IPAM changed under it.
            if raise_on_failure:
                raise ValidationError({'address': resolution.problem})
            self.status = OnboardingStatusChoices.STATUS_UNRESOLVED
            self.error = resolution.problem
            self._resolved = True
            return

        self.poller = DiscoveryPoller.objects.get_or_create(
            name=resolution.poller_name
        )[0]
        self._resolved = True

    def clean(self):
        super().clean()
        self.resolve_target(raise_on_failure=self._state.adding)

    def save(self, *args, **kwargs):
        # Only on create, or when a retry explicitly asks for it. Re-resolving
        # on every save would let a request change poller midway through being
        # scanned, and would re-query IPAM on every status transition.
        if self._state.adding and not getattr(self, '_resolved', False):
            self.resolve_target()
        super().save(*args, **kwargs)

    @property
    def target_site(self):
        """Where the device will actually be created."""
        return self.override_site or self.site

    @property
    def needs_a_site(self):
        """True when nothing can be applied until someone picks a site.

        Happens when no prefix matched and the default region supplied the
        poller: we can scan the device, but nothing tells us where it lives.
        """
        return self.target_site is None

    @property
    def is_open(self):
        return self.status not in OnboardingStatusChoices.TERMINAL

    @property
    def needs_attention(self):
        return self.status in OnboardingStatusChoices.NEEDS_ATTENTION

    @property
    def claim_is_fresh(self):
        """Is a poller believed to be working on this right now?

        Handing the same request to two pollers means scanning the device twice
        and, for an apply, racing to create the same objects. A claim is how
        one of them is told to leave it alone.
        """
        if self.claimed_at is None:
            return False
        timeout = timedelta(minutes=plugin_setting('claim_timeout_minutes'))
        return timezone.now() - self.claimed_at <= timeout

    @property
    def claim_expired(self):
        """Has a claimed request been held too long to still be running?

        A poller that dies mid-scan would otherwise leave the request stuck in
        `scanning` with nothing to move it on.
        """
        if self.status != OnboardingStatusChoices.STATUS_SCANNING or self.claimed_at is None:
            return False
        return not self.claim_is_fresh

    @property
    def waiting_on(self):
        """One line saying who owes the next move, for the list and detail views."""
        if self.status == OnboardingStatusChoices.STATUS_PENDING:
            if self.poller is None:
                return 'No poller assigned'
            if self.poller.is_stale:
                return 'Poller %s has not checked in' % self.poller.name
            return 'Poller %s, next check-in' % self.poller.name
        if self.status == OnboardingStatusChoices.STATUS_SCANNING:
            return 'Scan in progress' if not self.claim_expired else 'Scan stalled, will retry'
        if self.status == OnboardingStatusChoices.STATUS_REVIEW:
            if self.needs_a_site:
                return 'You — no prefix matched, so pick a site'
            return 'You — review what was found'
        if self.status == OnboardingStatusChoices.STATUS_APPROVED:
            if self.poller is None:
                return 'Approved, but no poller owns this address'
            return 'Poller %s, to apply' % self.poller.name
        return ''

    # --- The discovered payload, unpacked for templates. Kept as properties so
    # a partial or oddly-shaped report from an older poller degrades to blanks
    # rather than raising halfway down a page.

    @property
    def discovered_devices(self):
        return self.discovered.get('devices', []) if self.discovered else []

    @property
    def primary_discovered(self):
        devices = self.discovered_devices
        if not devices:
            return {}
        for entry in devices:
            if entry.get('is_master'):
                return entry
        return devices[0]

    @property
    def discovered_name(self):
        return self.override_name or self.primary_discovered.get('name', '')

    @property
    def effective_model(self):
        """The model that will be used, whoever supplied it.

        What the device reported wins. A platform that publishes no model at
        all — a Firepower 2120 among them — leaves the override as the only
        way the device can be created, since NetBox needs a device type and
        this scanner will not derive one from sysObjectID.
        """
        return (self.primary_discovered.get('model') or '').strip() \
            or self.override_model.strip()

    @property
    def discovered_model(self):
        return self.primary_discovered.get('model', '')

    @property
    def discovered_serial(self):
        return self.primary_discovered.get('serial', '')

    @property
    def discovered_manufacturer(self):
        return self.primary_discovered.get('manufacturer', '')

    @property
    def discovered_version(self):
        return self.primary_discovered.get('software_version', '')

    @property
    def discovered_platform(self):
        return self.primary_discovered.get('platform', '')

    @property
    def stripped_domain_not_applied(self):
        """Did the poller name this device without the stripped-domain list?

        A poller that cannot read the list falls back to cutting the hostname
        at the first dot, and nothing else about the scan looks wrong: the
        device simply arrives as `test` when `test.example` was meant. It
        happens when the poller runs a build from before the list existed,
        when its token cannot view stripped domains, or when the domain was
        listed after the scan -- all invisible from here unless this says so.

        Returns {'named', 'expected', 'domain'} when the reported name is the
        first label of a hostname that an enabled domain would have left more
        of, else None. A diagnosis only: the poller names devices, and the
        one line of suffix arithmetic here decides nothing.
        """
        hostname = ((self.discovered or {}).get('sys_name') or '').strip().rstrip('.')
        named = (self.primary_discovered.get('name') or '').strip()
        if not hostname or not named or '.' in named:
            return None
        if named.lower() != hostname.split('.')[0].lower():
            return None     # not a first-dot cut: a rule, an override, a typed name
        lowered = hostname.lower()
        domains = sorted(
            StrippedDomain.objects.filter(enabled=True).values_list('domain', flat=True),
            key=len, reverse=True,
        )
        for domain in domains:
            suffix = '.' + domain
            if lowered.endswith(suffix) and len(hostname) > len(suffix):
                expected = hostname[:-len(suffix)]
                if expected.lower() != named.lower():
                    return {'named': named, 'expected': expected, 'domain': domain}
                return None
        return None

    @property
    def discovered_context(self):
        """What partition of a chassis this scan is, if it is one.

        A Nexus VDC or a vCMP guest, as the poller reported it: kind,
        chassis_serial, name, identifier and a one-line detail. None for a
        box of its own.
        """
        return self.primary_discovered.get('context') or None

    @property
    def is_stack(self):
        return len(self.discovered_devices) > 1

    @property
    def interface_count(self):
        return sum(len(d.get('interfaces', [])) for d in self.discovered_devices)

    @property
    def module_count(self):
        return sum(len(d.get('modules', [])) for d in self.discovered_devices)

    @property
    def access_point_count(self):
        return len(self.discovered.get('access_points', [])) if self.discovered else 0

    @property
    def rules_applied(self):
        """Values a discovery rule supplied rather than the device.

        Each entry names the rule, the device (a stack member has its own),
        the field, the value written and what the device had said, so the
        page can show which facts are the device's own.
        """
        return (self.discovered.get('rules_applied') or []) if self.discovered else []


class HardwareReplacement(PrimaryModel):
    """A serial number that changed under a name we already knew.

    A rescan finding a different serial at the same place means the metal was
    swapped — an RMA, a spare pulled off the shelf, a line card replaced. The
    inventory has to follow the new unit, but the old serial must not simply be
    overwritten: serials are what support contracts and quotes are matched on,
    so losing one silently means losing the thread on a box that may still be
    under contract, or still sitting in a rack somewhere.

    NetBox's changelog does record the old value, but only as a diff on one
    object at one moment. This is the queryable version: every swap, with both
    serials, ready to be reported on.

    For a chassis the old Device record is kept as well, retired rather than
    deleted, and `replaced_device` points at it. For a module it cannot be —
    Module.module_bay is not nullable, so the old row has nowhere to live once
    the new part is in the bay — and this record is the only surviving trace.
    That asymmetry is the reason this model exists at all.
    """

    kind = models.CharField(
        max_length=20, choices=ReplacementKindChoices,
        default=ReplacementKindChoices.KIND_CHASSIS,
    )
    device = models.ForeignKey(
        to='dcim.Device', on_delete=models.CASCADE,
        related_name='hardware_replacements',
        help_text='The device as it stands now, carrying the new serial',
    )
    replaced_device = models.ForeignKey(
        to='dcim.Device', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='+',
        help_text='The retired record for the unit that was removed, for a chassis swap',
    )
    module_bay = models.CharField(
        max_length=100, blank=True,
        help_text='Which bay, for a module swap',
    )
    old_serial = models.CharField(max_length=100)
    new_serial = models.CharField(max_length=100)
    model_name = models.CharField(
        max_length=100, blank=True, verbose_name='Model',
        help_text='The hardware model reported at the time of the swap',
    )
    detected_at = models.DateTimeField()
    poller = models.ForeignKey(
        to='netbox_discovery.DiscoveryPoller', on_delete=models.SET_NULL,
        blank=True, null=True, related_name='+',
    )

    clone_fields = ()

    class Meta:
        ordering = ('-detected_at',)
        verbose_name = 'hardware replacement'
        verbose_name_plural = 'hardware replacements'
        indexes = (
            models.Index(fields=('old_serial',)),
            models.Index(fields=('new_serial',)),
            models.Index(fields=('-detected_at',)),
        )

    def __str__(self):
        return '%s: %s -> %s' % (self.device, self.old_serial or '?', self.new_serial or '?')

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:hardwarereplacement', args=[self.pk])

    def get_kind_color(self):
        return ReplacementKindChoices.colors.get(self.kind)


class DiscoveryIssue(PrimaryModel):
    """Something a scan found that a person has to settle.

    The scanner's job is to record what devices say about themselves. When two
    of them say something that cannot both be true, it must not pick a winner —
    it stops, leaves the existing record alone, and says so here.

    The case this was built for: a device reporting a serial that NetBox
    already holds against a different device. Matching on serial is what makes
    a re-IP'd or renamed box resolve to its existing record, and it is also
    what lets one device's data be written straight over another's when a
    serial is duplicated or mistyped. That overwrite is silent and destroys the
    record it lands on, so it used to be refused and raised here.

    Duplicate serials are allowed now (a Nexus VDC and its chassis, a vCMP
    guest and its host, a vendor reusing a number), and the scanner writes a
    scan that agrees with an existing record on neither name nor address as a
    separate device instead of refusing it. Nothing raises this kind any more;
    the model stays for the issues already filed and for whatever a scan may
    next be unable to decide.
    """

    kind = models.CharField(
        max_length=30, choices=IssueKindChoices,
        default=IssueKindChoices.KIND_DUPLICATE_SERIAL,
    )
    status = models.CharField(
        max_length=20, choices=IssueStatusChoices,
        default=IssueStatusChoices.STATUS_OPEN,
    )
    address = models.CharField(
        max_length=64, blank=True, verbose_name='Scanned address',
        help_text='The address being scanned when this came up',
    )
    device = models.ForeignKey(
        to='dcim.Device', on_delete=models.CASCADE, blank=True, null=True,
        related_name='discovery_issues',
        help_text='The existing record the scan collided with',
    )
    serial = models.CharField(max_length=100, blank=True)
    reported_name = models.CharField(
        max_length=100, blank=True,
        help_text='The hostname the scanned device gave for itself',
    )
    detail = models.TextField(
        help_text='What the poller could not decide, in words',
    )
    detected_at = models.DateTimeField()
    last_seen_at = models.DateTimeField(
        blank=True, null=True,
        help_text='When a scan last hit this same problem',
    )
    poller = models.ForeignKey(
        to='netbox_discovery.DiscoveryPoller', on_delete=models.SET_NULL,
        blank=True, null=True, related_name='+',
    )

    clone_fields = ('kind',)

    class Meta:
        ordering = ('-detected_at',)
        verbose_name = 'discovery issue'
        verbose_name_plural = 'discovery issues'
        indexes = (
            models.Index(fields=('status',)),
            models.Index(fields=('serial',)),
        )
        constraints = (
            # One open issue per address and serial. A sweep every six hours
            # would otherwise file the same complaint four times a day until
            # somebody dealt with it, and burying the list is how it stops
            # being read.
            models.UniqueConstraint(
                fields=('address', 'serial', 'kind'),
                condition=models.Q(status='open'),
                name='netbox_discovery_unique_open_issue',
            ),
        )

    def __str__(self):
        return '%s: %s' % (self.get_kind_display(), self.address or self.serial)

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:discoveryissue', args=[self.pk])

    def get_kind_color(self):
        return IssueKindChoices.colors.get(self.kind)

    def get_status_color(self):
        return IssueStatusChoices.colors.get(self.status)

    @property
    def is_open(self):
        return self.status == IssueStatusChoices.STATUS_OPEN


class DiscoveryRule(PrimaryModel):
    """Fill in a fact the device does not report, for every device of one kind.

    The scanner records what a device says about itself and nothing else. When
    a platform publishes no model — a Firepower 2120, say — the request stops
    at review and somebody types the model in. By the third identical firewall
    the typing is the problem, and a rule says it once: when the name contains
    "fw-" and the model is empty, the model is FPR-2120.

    Rules are applied by the pollers, not here. A poller reads the enabled
    rules at the start of each run and applies them to every scan before it is
    reported or written, so one rule serves onboarding scans and the sweep
    alike, and the review page shows the result with the rule named against
    it. Nothing in NetBox evaluates a rule, so there is one implementation to
    keep correct rather than two that can drift — the same reason the apply
    itself lives in the scanner.

    By default a rule fills a field only when the device left it empty. What
    the device reports wins, exactly as a reviewer's model override does. A
    rule can be set to replace instead; the value it replaced is recorded with
    the scan so the substitution is visible rather than silent.
    """

    name = models.CharField(max_length=100, unique=True)
    enabled = models.BooleanField(
        default=True, help_text='Pollers apply only enabled rules',
    )
    weight = models.PositiveSmallIntegerField(
        default=100,
        help_text='Rules apply in ascending weight. Where two would set the same '
                  'field the lighter one wins, and a later rule can match on '
                  'what an earlier one set.',
    )
    match_field = models.CharField(
        max_length=30, choices=RuleMatchFieldChoices,
        default=RuleMatchFieldChoices.FIELD_NAME, verbose_name='When this field',
    )
    match_operator = models.CharField(
        max_length=20, choices=RuleOperatorChoices,
        default=RuleOperatorChoices.OP_CONTAINS, verbose_name='Comparison',
    )
    match_value = models.CharField(
        max_length=200, verbose_name='This value',
        help_text='Matching is case-insensitive',
    )
    set_field = models.CharField(
        max_length=30, choices=RuleSetFieldChoices,
        default=RuleSetFieldChoices.FIELD_MODEL, verbose_name='Set this field',
    )
    set_value = models.CharField(
        max_length=200, verbose_name='To this value',
        help_text='Exactly as it should appear in NetBox, e.g. FPR-2120',
    )
    only_if_blank = models.BooleanField(
        default=True, verbose_name='Only when empty',
        help_text='Fill the field only when the device did not report one. Turn '
                  'off to replace what the device says; the replaced value is '
                  'still recorded on the scan.',
    )

    clone_fields = ('enabled', 'weight', 'match_field', 'match_operator',
                    'set_field', 'only_if_blank')

    class Meta:
        ordering = ('weight', 'name')
        verbose_name = 'discovery rule'
        verbose_name_plural = 'discovery rules'

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:discoveryrule', args=[self.pk])

    def clean(self):
        super().clean()
        # Refused here rather than on the poller, which would only be able to
        # skip the rule and log about it on a box nobody is watching.
        if not (self.match_value or '').strip():
            raise ValidationError({'match_value': 'Enter the value to match.'})
        if not (self.set_value or '').strip():
            raise ValidationError({'set_value': 'Enter the value to set.'})
        if self.match_operator == RuleOperatorChoices.OP_REGEX:
            try:
                re.compile(self.match_value)
            except re.error as exc:
                raise ValidationError({
                    'match_value': 'Not a valid regular expression: %s.' % exc,
                })
        if self.set_field == RuleSetFieldChoices.FIELD_SERIAL:
            # A serial belongs to one box. Anything looser than an exact
            # match on the name or address stamps the same serial on every
            # device the rule matches, and the sync refuses the second as a
            # duplicate — after the first has already been written wrong.
            pinned = (
                self.match_field in (RuleMatchFieldChoices.FIELD_NAME,
                                     RuleMatchFieldChoices.FIELD_ADDRESS)
                and self.match_operator == RuleOperatorChoices.OP_EQUALS
            )
            if not pinned:
                raise ValidationError({
                    'set_field': 'A serial belongs to one device, so a rule that '
                                 'sets it must match the device name or scanned '
                                 'address with "is exactly".',
                })
            # A changed serial is how a hardware swap is detected; a rule
            # replacing one would retire a device record on every sweep.
            if not self.only_if_blank:
                raise ValidationError({
                    'only_if_blank': 'A rule may fill in a serial the device does '
                                     'not report, never replace one it does.',
                })

    @staticmethod
    def _lower_first(label):
        return label[:1].lower() + label[1:] if label else label

    @property
    def sentence(self):
        """The rule in words, for the list, the detail page and the API."""
        subject = self._lower_first(self.get_match_field_display())
        target = self._lower_first(self.get_set_field_display())
        text = 'When the %s %s “%s”' % (
            subject, self.get_match_operator_display(), self.match_value,
        )
        if self.only_if_blank:
            return '%s and the %s is empty, set the %s to “%s”.' % (
                text, target, target, self.set_value,
            )
        return '%s, set the %s to “%s”, replacing whatever the device reports.' % (
            text, target, self.set_value,
        )


class StrippedDomain(PrimaryModel):
    """A domain to take off the hostnames devices report.

    A device calls itself `test.google.com`; NetBox should call it `test`.
    The scanner used to get there by keeping everything before the first dot,
    which is wrong wherever the hostname itself has dots in it:
    `sw1.floor2.google.com` and `sw1.floor3.google.com` both became `sw1`,
    and the second could never be created beside the first. So the domains
    are said out loud instead. With `google.com` on this list:

        test.google.com          ->  test
        sw1.floor2.google.com    ->  sw1.floor2
        sw1.other.net            ->  sw1.other.net    (not on the list)

    The dot that joins the domain to the hostname is inferred and comes off
    with it. A domain is only removed from the end of a name and only at a
    label boundary, so `testgoogle.com` is untouched, and the longest listed
    match wins.

    Pollers read the enabled entries at the start of every run; nothing in
    NetBox renames anything. While the list is EMPTY the first-dot rule still
    applies, so listing a first domain is a switch as well as an entry: from
    then on dots are kept, and a hostname under a domain that is not listed
    keeps that domain. Existing devices are never given a longer name, and a
    domain listed late is taken off the devices that were created with it on.
    """

    domain = models.CharField(
        max_length=253, unique=True,
        help_text='Removed from the end of reported hostnames, with the dot that '
                  'joins it: google.com turns test.google.com into test',
    )
    enabled = models.BooleanField(
        default=True, help_text='Pollers strip only enabled domains',
    )

    clone_fields = ('enabled',)

    class Meta:
        ordering = ('domain',)
        verbose_name = 'stripped domain'
        verbose_name_plural = 'stripped domains'

    def __str__(self):
        return self.domain

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:strippeddomain', args=[self.pk])

    @staticmethod
    def normalise(value):
        """As the pollers compare it: lowercase, no surrounding dots or space."""
        return (value or '').strip().strip('.').strip().lower()

    def clean(self):
        self.domain = self.normalise(self.domain)
        super().clean()
        if not self.domain:
            raise ValidationError({'domain': 'Enter the domain to strip, e.g. google.com.'})
        if any(ch.isspace() for ch in self.domain) or '..' in self.domain:
            raise ValidationError({
                'domain': 'A domain is labels joined by single dots, with no spaces.',
            })
        clash = StrippedDomain.objects.filter(domain=self.domain).exclude(pk=self.pk)
        if clash.exists():
            raise ValidationError({'domain': '%s is already on the list.' % self.domain})

    def save(self, *args, **kwargs):
        # Here as well as in clean(): the REST API validates a throwaway
        # instance and saves the raw values, so ".Google.com." would otherwise
        # be stored as typed when it arrives that way.
        self.domain = self.normalise(self.domain)
        super().save(*args, **kwargs)

    def example(self):
        """What this entry does, for the list and the detail page."""
        return 'switch.%s → switch' % self.domain


# Imported here so Django discovers these models with the rest of the plugin.
from .upgrade_models import UpgradeDependency, UpgradeGroup, UpgradeJob  # noqa: E402,F401
from .command_models import CommandOutput  # noqa: E402,F401
