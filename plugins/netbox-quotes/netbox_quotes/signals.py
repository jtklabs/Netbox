"""Keep quote-line assignments consistent when target hardware is deleted."""

from dcim.models import Device, InventoryItem, Module
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from netbox_quotes.choices import MatchStateChoices
from netbox_quotes.models import QuoteLine


@receiver(pre_delete, sender=Device)
@receiver(pre_delete, sender=Module)
@receiver(pre_delete, sender=InventoryItem)
def clear_quote_line_assignments(sender, instance, **kwargs):
    QuoteLine.objects.filter(
        assigned_object_type=ContentType.objects.get_for_model(sender),
        assigned_object_id=instance.pk,
    ).update(
        assigned_object_type=None,
        assigned_object_id=None,
        match_state=MatchStateChoices.STATE_UNMATCHED,
    )
