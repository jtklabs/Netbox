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
)
from netbox_discovery.utils import plugin_setting

__all__ = (
    'DiscoveryIssue',
    'DiscoveryPoller',
    'HardwareReplacement',
    'OnboardingRequest',
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

    def get_status_color(self):
        return 'red' if self.is_stale else 'green'

    @property
    def sites(self):
        from netbox_discovery.resolution import sites_for_poller

        return sites_for_poller(self.name)

    def touch(self, version='', summary=''):
        """Record a check-in without writing a changelog entry.

        Deliberately a queryset update. A poller checking in every few minutes
        would otherwise write an ObjectChange per poller per interval and bury
        every change that matters under a wall of heartbeats.
        """
        now = timezone.now()
        fields = {'last_seen_at': now}
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
        related_name='+', help_text='Narrows the address to one routing table',
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
    record it lands on, so it is refused and raised here instead.
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
