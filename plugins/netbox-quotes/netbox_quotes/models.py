from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.urls import reverse
from netbox.models import PrimaryModel
from utilities.querysets import RestrictedQuerySet

from netbox_quotes.choices import MatchStateChoices, QuoteStatusChoices

__all__ = ('QuoteVendor', 'Vendor', 'Quote', 'QuoteLine')

QUOTE_LINE_ASSIGNMENT_MODELS = Q(
    app_label='dcim', model__in=('device', 'module', 'inventoryitem')
)


class QuoteVendor(PrimaryModel):
    """A vendor/VAR we receive support quotes from.

    Named QuoteVendor (not Vendor) because netbox_lifecycle also defines a Vendor
    model and NetBox's Owner reverse accessors would clash on the class name.
    """

    name = models.CharField(max_length=100, unique=True)
    portal_url = models.URLField(blank=True)

    class Meta:
        ordering = ('name',)
        verbose_name = 'vendor'
        verbose_name_plural = 'vendors'

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_quotes:quotevendor', args=[self.pk])


# Internal alias so the rest of the plugin can keep saying Vendor.
Vendor = QuoteVendor


class Quote(PrimaryModel):
    vendor = models.ForeignKey(
        to=QuoteVendor, on_delete=models.PROTECT, related_name='quotes'
    )
    number = models.CharField(max_length=100)
    status = models.CharField(
        max_length=30,
        choices=QuoteStatusChoices,
        default=QuoteStatusChoices.STATUS_RECEIVED,
    )
    quote_date = models.DateField(blank=True, null=True)
    valid_until = models.DateField(blank=True, null=True)
    currency = models.CharField(max_length=3, default='USD')
    document = models.FileField(upload_to='netbox-quotes/', blank=True)

    clone_fields = ('vendor', 'status', 'currency')

    class Meta:
        ordering = ('-quote_date', 'number')
        constraints = (
            models.UniqueConstraint(
                'vendor',
                'number',
                name='%(app_label)s_%(class)s_unique_vendor_number',
                violation_error_message='Quote numbers must be unique per vendor.',
            ),
        )

    def __str__(self):
        return f'{self.vendor.name} {self.number}'

    def get_absolute_url(self):
        return reverse('plugins:netbox_quotes:quote', args=[self.pk])

    def get_status_color(self):
        return QuoteStatusChoices.colors.get(self.status)

    @property
    def total(self):
        totals = [
            line.effective_total
            for line in self.lines.all()
            if line.effective_total is not None
        ]
        return sum(totals) if totals else None


class QuoteLineQuerySet(RestrictedQuerySet):
    def for_device(self, device):
        """Lines assigned to a device directly or to any of its modules/inventory items."""
        return self.for_devices([device.pk])

    def for_devices(self, device_pks):
        from dcim.models import Device, InventoryItem, Module

        return self.filter(
            Q(
                assigned_object_type=ContentType.objects.get_for_model(Device),
                assigned_object_id__in=device_pks,
            )
            | Q(
                assigned_object_type=ContentType.objects.get_for_model(Module),
                assigned_object_id__in=Module.objects.filter(
                    device__in=device_pks
                ).values('pk'),
            )
            | Q(
                assigned_object_type=ContentType.objects.get_for_model(InventoryItem),
                assigned_object_id__in=InventoryItem.objects.filter(
                    device__in=device_pks
                ).values('pk'),
            )
        )


class QuoteLine(PrimaryModel):
    quote = models.ForeignKey(to=Quote, on_delete=models.CASCADE, related_name='lines')
    line_number = models.PositiveIntegerField(blank=True, null=True)
    part_number = models.CharField(
        max_length=100, blank=True, help_text='Hardware part/model number on the line'
    )
    service_sku = models.CharField(
        max_length=100, blank=True, help_text='Support service SKU (e.g. CON-SNT-...)'
    )
    serial = models.CharField(max_length=100, blank=True, db_index=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True
    )
    line_total = models.DecimalField(
        max_digits=12, decimal_places=2, blank=True, null=True
    )
    coverage_start = models.DateField(blank=True, null=True)
    coverage_end = models.DateField(blank=True, null=True)

    assigned_object_type = models.ForeignKey(
        to=ContentType,
        limit_choices_to=QUOTE_LINE_ASSIGNMENT_MODELS,
        on_delete=models.PROTECT,
        related_name='+',
        blank=True,
        null=True,
    )
    assigned_object_id = models.PositiveBigIntegerField(blank=True, null=True)
    assigned_object = GenericForeignKey(
        ct_field='assigned_object_type', fk_field='assigned_object_id'
    )
    match_state = models.CharField(
        max_length=30,
        choices=MatchStateChoices,
        default=MatchStateChoices.STATE_UNMATCHED,
    )

    objects = QuoteLineQuerySet.as_manager()

    clone_fields = ('quote', 'service_sku', 'coverage_start', 'coverage_end')

    class Meta:
        ordering = ('quote', 'line_number', 'pk')
        indexes = (
            models.Index(fields=('assigned_object_type', 'assigned_object_id')),
        )

    def __str__(self):
        label = self.description or self.part_number or self.serial or f'#{self.pk}'
        return f'{self.quote.number} · {label}'

    def get_absolute_url(self):
        return reverse('plugins:netbox_quotes:quoteline', args=[self.pk])

    def get_match_state_color(self):
        return MatchStateChoices.colors.get(self.match_state)

    @property
    def effective_total(self):
        if self.line_total is not None:
            return self.line_total
        if self.unit_price is not None:
            return self.unit_price * self.quantity
        return None

    @property
    def device(self):
        """The device this line ultimately covers (directly or via a component)."""
        obj = self.assigned_object
        if obj is None:
            return None
        return obj if obj._meta.model_name == 'device' else obj.device

    def save(self, *args, **kwargs):
        from netbox_quotes.matching import match_line

        if self.assigned_object_id:
            # A present assignment on a line not matched automatically means a
            # human placed or confirmed it.
            if self.match_state in (
                MatchStateChoices.STATE_UNMATCHED,
                MatchStateChoices.STATE_AMBIGUOUS,
            ):
                self.match_state = MatchStateChoices.STATE_MANUAL
        elif self.serial:
            match_line(self)
        else:
            self.match_state = MatchStateChoices.STATE_UNMATCHED
        super().save(*args, **kwargs)
