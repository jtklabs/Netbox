from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from netbox.models import PrimaryModel

from netbox_refresh.choices import LifecycleSourceChoices, LifecycleStatusChoices

__all__ = ('ModelLifecycle',)

# EoL is tracked per hardware MODEL, not per unit: a device type or a module
# type. That matches how vendors publish it (one bulletin per PID).
LIFECYCLE_ASSIGNMENT_MODELS = Q(app_label='dcim', model__in=('devicetype', 'moduletype'))


class ModelLifecycle(PrimaryModel):
    """End-of-life dates, successor model and replacement cost for one hardware model."""

    assigned_object_type = models.ForeignKey(
        to=ContentType,
        limit_choices_to=LIFECYCLE_ASSIGNMENT_MODELS,
        on_delete=models.CASCADE,
        related_name='+',
    )
    assigned_object_id = models.PositiveBigIntegerField()
    assigned_object = GenericForeignKey(
        ct_field='assigned_object_type', fk_field='assigned_object_id'
    )

    # --- Lifecycle dates. Names follow Cisco's EoX vocabulary because that is
    # the most detailed vendor feed we consume; they map cleanly onto other
    # vendors' coarser announcements.
    announcement_date = models.DateField(
        blank=True, null=True, help_text='Date the end-of-life was announced'
    )
    end_of_sale = models.DateField(blank=True, null=True)
    end_of_sw_maintenance = models.DateField(
        blank=True, null=True, verbose_name='End of software maintenance'
    )
    end_of_security_support = models.DateField(blank=True, null=True)
    end_of_routine_failure_analysis = models.DateField(blank=True, null=True)
    end_of_service_attach = models.DateField(
        blank=True, null=True, help_text='Last date to attach a new support contract'
    )
    end_of_service_contract_renewal = models.DateField(blank=True, null=True)
    end_of_support = models.DateField(
        blank=True, null=True,
        help_text='Last date of support — the date refresh reporting works from',
    )

    bulletin_number = models.CharField(max_length=50, blank=True)
    bulletin_url = models.URLField(max_length=500, blank=True)

    # --- Replacement. Two nullable FKs rather than a second generic relation:
    # a device type is replaced by a device type, a module type by a module
    # type, and explicit FKs keep filtering and forms simple.
    replacement_device_type = models.ForeignKey(
        to='dcim.DeviceType', on_delete=models.SET_NULL,
        blank=True, null=True, related_name='replaces_lifecycles',
    )
    replacement_module_type = models.ForeignKey(
        to='dcim.ModuleType', on_delete=models.SET_NULL,
        blank=True, null=True, related_name='replaces_lifecycles',
    )
    replacement_notes = models.TextField(
        blank=True,
        help_text="Vendor migration guidance when no single successor model applies",
    )

    # --- Cost of replacing ONE unit of this model.
    replacement_cost = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True,
        help_text='Cost to replace a single unit',
    )
    currency = models.CharField(max_length=3, default='USD')
    cost_updated = models.DateField(blank=True, null=True)

    source = models.CharField(
        max_length=30,
        choices=LifecycleSourceChoices,
        default=LifecycleSourceChoices.SOURCE_MANUAL,
    )
    last_synced = models.DateTimeField(blank=True, null=True)

    clone_fields = ('currency', 'source')

    class Meta:
        ordering = ('end_of_support', 'pk')
        verbose_name = 'model lifecycle'
        verbose_name_plural = 'model lifecycles'
        constraints = (
            models.UniqueConstraint(
                'assigned_object_type', 'assigned_object_id',
                name='%(app_label)s_%(class)s_unique_object',
                violation_error_message='This hardware model already has a lifecycle record.',
            ),
        )
        indexes = (
            models.Index(fields=('assigned_object_type', 'assigned_object_id')),
            models.Index(fields=('end_of_support',)),
        )

    def __str__(self):
        if self.assigned_object:
            return str(self.assigned_object)
        return 'Lifecycle %s' % self.pk

    def get_absolute_url(self):
        return reverse('plugins:netbox_refresh:modellifecycle', args=[self.pk])

    def clean(self):
        super().clean()
        if self.replacement_device_type and self.replacement_module_type:
            raise ValidationError(
                'Set only one replacement — a device type or a module type, not both.'
            )
        obj = self.assigned_object
        if obj is not None:
            model_name = obj._meta.model_name
            if model_name == 'devicetype' and self.replacement_module_type:
                raise ValidationError(
                    {'replacement_module_type': 'A device type must be replaced by a device type.'}
                )
            if model_name == 'moduletype' and self.replacement_device_type:
                raise ValidationError(
                    {'replacement_device_type': 'A module type must be replaced by a module type.'}
                )
            if self.replacement_device_type_id == self.assigned_object_id and model_name == 'devicetype':
                raise ValidationError({'replacement_device_type': 'A model cannot replace itself.'})

    # ------------------------------------------------------------------ #
    @property
    def replacement(self):
        return self.replacement_device_type or self.replacement_module_type

    @property
    def part_number(self):
        return getattr(self.assigned_object, 'part_number', '') or ''

    @property
    def manufacturer(self):
        return getattr(self.assigned_object, 'manufacturer', None)

    @property
    def installed_count(self):
        """How many units of this model are installed."""
        obj = self.assigned_object
        if obj is None:
            return 0
        from dcim.models import Device, Module

        if obj._meta.model_name == 'devicetype':
            return Device.objects.filter(device_type=obj).count()
        return Module.objects.filter(module_type=obj).count()

    @property
    def extended_cost(self):
        """Replacement cost for every installed unit of this model.

        The cost is coerced rather than used as-is: an import or API write can
        leave a string on the in-memory instance, and multiplying a string by an
        int repeats it instead of failing.
        """
        if self.replacement_cost is None:
            return None
        return Decimal(self.replacement_cost) * self.installed_count

    @property
    def status(self):
        today = date.today()
        if self.end_of_support and self.end_of_support <= today:
            return LifecycleStatusChoices.STATUS_END_OF_SUPPORT
        if self.end_of_sale and self.end_of_sale <= today:
            return LifecycleStatusChoices.STATUS_END_OF_SALE
        if self.end_of_sale or self.end_of_support or self.announcement_date:
            return LifecycleStatusChoices.STATUS_EOS_ANNOUNCED
        return LifecycleStatusChoices.STATUS_UNKNOWN

    def get_status_display(self):
        # CHOICES entries are (value, label, color) triples, so build the label
        # map explicitly rather than calling dict() on them.
        labels = {entry[0]: entry[1] for entry in LifecycleStatusChoices.CHOICES}
        return labels.get(self.status, self.status)

    def get_status_color(self):
        return LifecycleStatusChoices.colors.get(self.status)

    def get_source_color(self):
        return LifecycleSourceChoices.colors.get(self.source)
