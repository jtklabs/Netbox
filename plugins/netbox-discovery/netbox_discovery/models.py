from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from netbox.models import PrimaryModel

from netbox_discovery.choices import OnboardingStatusChoices
from netbox_discovery.utils import plugin_setting

__all__ = ('DiscoveryPoller', 'OnboardingRequest')


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

        resolution = resolve(self.address)
        self.address = resolution.address
        # Partial results are kept, so the detail page can show how far the
        # chain got before it broke rather than just saying "no".
        self.prefix = resolution.prefix
        if resolution.site is not None:
            self.site = resolution.site

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
    def is_open(self):
        return self.status not in OnboardingStatusChoices.TERMINAL

    @property
    def needs_attention(self):
        return self.status in OnboardingStatusChoices.NEEDS_ATTENTION

    @property
    def claim_expired(self):
        """Has a claimed request been held too long to still be running?

        A poller that dies mid-scan would otherwise leave the request stuck in
        `scanning` with nothing to move it on.
        """
        if self.status != OnboardingStatusChoices.STATUS_SCANNING or self.claimed_at is None:
            return False
        timeout = timedelta(minutes=plugin_setting('claim_timeout_minutes'))
        return timezone.now() - self.claimed_at > timeout

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
