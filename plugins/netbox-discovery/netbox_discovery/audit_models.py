"""Database-backed recurring, read-only configuration audits."""
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from netbox.models import PrimaryModel


class AuditSchedule(PrimaryModel):
    name = models.CharField(max_length=100, unique=True)
    enabled = models.BooleanField(default=True)
    frequency = models.CharField(max_length=10, choices=(('daily', 'Daily'), ('weekly', 'Weekly')), default='daily')
    weekday = models.PositiveSmallIntegerField(default=0, choices=list(enumerate(
        ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'))))
    local_time = models.TimeField(default=time(2))
    time_zone = models.CharField(max_length=64, default='UTC')
    window_hours = models.PositiveSmallIntegerField(default=4, validators=[MinValueValidator(1), MaxValueValidator(24)])
    filters = models.JSONField(default=dict)
    profile_source = models.CharField(max_length=10, default='custom', choices=(
        ('custom', 'Selected standards'), ('saved', 'Saved profile'), ('model', 'Model defaults')))
    saved_profile = models.ForeignKey('netbox_discovery.JobProfile', on_delete=models.PROTECT,
                                     blank=True, null=True, related_name='audit_schedules')
    profile = models.JSONField(default=dict, blank=True)
    run_as = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, blank=True, editable=False, related_name='+')
    next_run_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:auditschedule', args=[self.pk])

    @property
    def overdue(self):
        from datetime import timedelta
        return bool(self.enabled and self.next_run_at and self.next_run_at < timezone.now() - timedelta(minutes=5))

    def clean(self):
        super().clean()
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError({'time_zone': 'Enter an IANA time zone, such as America/New_York or UTC.'})
        allowed = {'status', 'tenant_id', 'site_id', 'platform_id', 'device_type_id', 'tag_id', 'id'}
        if not isinstance(self.filters, dict) or not self.filters or set(self.filters) - allowed:
            raise ValidationError({'filters': 'Select devices using status, tenant_id, site_id, platform_id, device_type_id, tag_id or id.'})
        if 'status' in self.filters and self.filters['status'] != ['active']:
            raise ValidationError({'filters': 'Recurring audits target active inventory; status must be ["active"].'})
        from dcim.filtersets import DeviceFilterSet
        for value in self.filters.values():
            if not isinstance(value, list) or not value or any(type(v) not in (int, str) or not str(v).strip() for v in value):
                raise ValidationError({'filters': 'Each filter must contain a nonempty list of values.'})
        selection = DeviceFilterSet(self.filters)
        if not selection.is_valid():
            raise ValidationError({'filters': str(selection.errors)})
        if self.profile_source == 'saved':
            if not self.saved_profile_id or self.saved_profile.kind != 'remediate':
                raise ValidationError({'saved_profile': 'Select a configuration remediation profile.'})
        elif self.saved_profile_id:
            raise ValidationError({'saved_profile': 'A saved profile requires the Saved profile source.'})
        if self.profile_source == 'custom':
            from .upgrade_queue import validate_remediation, QueueError
            try:
                validate_remediation(self.profile)
            except QueueError as exc:
                raise ValidationError({'profile': str(exc)}) from exc
        elif self.profile:
            raise ValidationError({'profile': 'Selected standards cannot be combined with a saved profile or model defaults.'})

    @transaction.atomic
    def save(self, *args, **kwargs):
        from .audit_scheduling import next_occurrence
        previous = type(self).objects.select_for_update().filter(pk=self.pk).first() if self.pk else None
        timing = ('enabled', 'frequency', 'weekday', 'local_time', 'time_zone')
        if not previous or any(getattr(previous, f) != getattr(self, f) for f in timing):
            self.next_run_at = next_occurrence(self, timezone.now()) if self.enabled else None
        else:
            # A form loaded before dispatch must not restore an already-consumed occurrence.
            self.next_run_at = previous.next_run_at
            self.last_run_at = previous.last_run_at
        super().save(*args, **kwargs)


class AuditRun(PrimaryModel):
    schedule = models.ForeignKey(AuditSchedule, on_delete=models.PROTECT, related_name='runs')
    scheduled_for = models.DateTimeField()
    dispatched_at = models.DateTimeField(default=timezone.now)
    outcome = models.CharField(max_length=10, choices=(('queued', 'Queued'), ('skipped', 'Skipped'), ('failed', 'Failed')))
    message = models.CharField(max_length=1000, blank=True)
    job_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ('-scheduled_for', '-pk')
        constraints = [models.UniqueConstraint(fields=('schedule', 'scheduled_for'), name='unique_audit_occurrence')]

    def __str__(self):
        return f'{self.schedule}: {self.scheduled_for.isoformat()}'

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:auditrun', args=[self.pk])
