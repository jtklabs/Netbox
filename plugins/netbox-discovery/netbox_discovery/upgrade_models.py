"""Per-device schedules, frozen on claim. Only the queue service changes state."""
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from netbox.models import PrimaryModel

from .upgrade_choices import ACTIVE, UpgradeGroupSourceChoices, UpgradeOperationChoices, UpgradeStatusChoices


class UpgradeJob(PrimaryModel):
    batch_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    device = models.ForeignKey('dcim.Device', on_delete=models.PROTECT, related_name='upgrade_jobs')
    poller = models.ForeignKey('netbox_discovery.DiscoveryPoller', on_delete=models.PROTECT,
                               related_name='upgrade_jobs')
    address = models.GenericIPAddressField(editable=False)
    device_name = models.CharField(max_length=64, editable=False)
    profile = models.JSONField(help_text='Snapshot of the validated IOS XE upgrade profile')
    operation = models.CharField(max_length=16, choices=UpgradeOperationChoices, default='audit')
    scheduled_at = models.DateTimeField()
    start_before = models.DateTimeField(help_text='Latest time device changes may begin; ongoing recovery continues')
    status = models.CharField(max_length=32, choices=UpgradeStatusChoices, default='pending')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                     null=True, related_name='+')
    claimed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.UUIDField(null=True, blank=True, editable=False)
    sequence = models.PositiveIntegerField(default=0, editable=False)
    stage = models.CharField(max_length=50, blank=True)
    message = models.CharField(max_length=1000, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    events = models.JSONField(default=list, blank=True)
    run_id = models.CharField(max_length=100, blank=True)
    # Frozen at scheduling, like the address and poller: the redundancy groups
    # this device was in and the downstream devices it waits for.
    groups = models.JSONField(default=list, blank=True)
    waits_for = models.JSONField(default=list, blank=True)
    held_reason = models.CharField(max_length=1000, blank=True)
    planned_wave = models.PositiveSmallIntegerField(default=1)
    # Job ids whose failure a person acknowledged when releasing this job's
    # hold, so the queue does not hold it again for the same reason.
    acknowledged = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ('-scheduled_at', '-pk')
        permissions = [('run_upgradejob', 'Execute scheduled upgrade jobs'),
                       ('apply_upgradejob', 'Schedule image staging and upgrades')]
        indexes = [models.Index(fields=('poller', 'status', 'scheduled_at'), name='upgrade_due_idx')]
        # Recovery keeps the device fenced until a person has checked it.
        constraints = [models.UniqueConstraint(fields=('device',), condition=models.Q(status__in=ACTIVE),
                                               name='one_active_upgrade_per_device')]

    def __str__(self):
        return f'{self.device_name}: {self.get_operation_display()}'

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:upgradejob', args=[self.pk])

    def get_status_color(self):
        return 'red' if self.heartbeat_stale else UpgradeStatusChoices.colors.get(self.status)

    @property
    def heartbeat_stale(self):
        return bool(self.status in ACTIVE and self.last_seen_at
                    and self.last_seen_at < timezone.now() - timedelta(minutes=5))

    @property
    def needs_recovery(self):
        return self.status == 'recovery_required' or bool(self.started_at and self.heartbeat_stale)


class UpgradeGroup(PrimaryModel):
    """Devices of which at most max_concurrent may be upgrading at once; a pair is a limit of 1."""
    name = models.CharField(max_length=100, unique=True)
    max_concurrent = models.PositiveSmallIntegerField(default=1, help_text='Members that may hold an active upgrade job at the same time; 0 for all of them')
    source = models.CharField(max_length=20, choices=UpgradeGroupSourceChoices, default='manual')
    key = models.CharField(max_length=200, blank=True, db_index=True, help_text='Identity of a discovered group, so a refresh updates it in place')
    stale = models.BooleanField(default=False, help_text='A discovered group the last refresh no longer saw')
    members = models.ManyToManyField('dcim.Device', related_name='upgrade_groups', blank=True)
    depends_on = models.ManyToManyField('self', symmetrical=False, related_name='dependents', blank=True,
                                        help_text='Groups whose members go first; the two groups never upgrade at the same time')

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:upgradegroup', args=[self.pk])


class UpgradeDependency(PrimaryModel):
    """The upstream device's job waits for the downstream device's job in the same batch."""
    upstream = models.ForeignKey('dcim.Device', on_delete=models.CASCADE, related_name='upgrade_downstream')
    downstream = models.ForeignKey('dcim.Device', on_delete=models.CASCADE, related_name='upgrade_upstream')
    source = models.CharField(max_length=20, choices=UpgradeGroupSourceChoices, default='manual')
    key = models.CharField(max_length=200, blank=True, db_index=True)
    stale = models.BooleanField(default=False)

    class Meta:
        ordering = ('upstream__name', 'downstream__name')
        constraints = [models.UniqueConstraint(fields=('upstream', 'downstream'), name='upgrade_dependency_unique')]

    def __str__(self):
        return f'{self.upstream} waits for {self.downstream}'

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:upgradedependency', args=[self.pk])

    def clean(self):
        super().clean()
        if self.upstream_id and self.upstream_id == self.downstream_id:
            from django.core.exceptions import ValidationError
            raise ValidationError('A device cannot wait for itself.')


class PrestagePolicy(PrimaryModel):
    """Keep the software standard's preferred image on every device of one model.

    The image, checksum and download link come from the Lifecycle plugin's
    standard for each device at the time of each run, so changing the standard
    is all it takes to start copying the new image ahead of the upgrade window.
    """
    device_type = models.OneToOneField('dcim.DeviceType', on_delete=models.CASCADE, related_name='prestage_policy',
                                       verbose_name='Model')
    enabled = models.BooleanField(default=True)
    interval_hours = models.PositiveSmallIntegerField(
        default=24, validators=[MinValueValidator(1)],
        help_text='Hours before a device that was already given this image is checked again')
    window_hours = models.PositiveSmallIntegerField(
        default=4, validators=[MinValueValidator(1)],
        help_text='Hours a staging job may wait for its poller before its start window closes')
    minimum_free_bytes = models.BigIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        help_text='Flash to keep free after the copy; empty uses the family minimum (1.5 GB, or 100 MB for EOS)')
    last_run_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_summary = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        ordering = ('device_type__manufacturer__name', 'device_type__model')
        verbose_name = 'prestage policy'
        verbose_name_plural = 'prestage policies'

    def __str__(self):
        return f'Prestage {self.device_type}'

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:prestagepolicy', args=[self.pk])
