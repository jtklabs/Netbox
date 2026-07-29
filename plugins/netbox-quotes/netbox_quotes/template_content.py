from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from netbox.plugins import PluginTemplateExtension

from netbox_quotes.models import QuoteLine


def _render_renewals_card(extension, lines, all_lines_url):
    totals = {}
    for line in lines:
        if line.effective_total is not None:
            currency = line.quote.currency
            totals[currency] = totals.get(currency, 0) + line.effective_total
    return extension.render(
        'netbox_quotes/inc/device_renewals.html',
        extra_context={
            'lines': lines[:10],
            'line_count': lines.count(),
            'totals': totals,
            'all_lines_url': all_lines_url,
        },
    )


class DeviceRenewalsCard(PluginTemplateExtension):
    """Support-renewal summary card on device pages (direct + component lines)."""

    models = ['dcim.device']

    def right_page(self):
        device = self.context.get('object')
        lines = QuoteLine.objects.for_device(device).prefetch_related(
            'quote', 'assigned_object'
        )
        url = (
            reverse('plugins:netbox_quotes:quoteline_list') + f'?device_id={device.pk}'
        )
        return _render_renewals_card(self, lines, url)


class ModuleRenewalsCard(PluginTemplateExtension):
    """Support-renewal summary card on module pages (lines assigned to this module)."""

    models = ['dcim.module']

    def right_page(self):
        module = self.context.get('object')
        ct = ContentType.objects.get_for_model(module)
        lines = QuoteLine.objects.filter(
            assigned_object_type=ct, assigned_object_id=module.pk
        ).prefetch_related('quote')
        url = (
            reverse('plugins:netbox_quotes:quoteline_list')
            + f'?assigned_object_type_id={ct.pk}&assigned_object_id={module.pk}'
        )
        return _render_renewals_card(self, lines, url)


template_extensions = (DeviceRenewalsCard, ModuleRenewalsCard)
