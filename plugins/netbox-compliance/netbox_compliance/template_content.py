"""A configuration-compliance card on the device page.

The card lists every standard in force that the device is in scope for, and the
verdict against each — including standards nobody has checked it against, which
render as Not checked rather than being left out. A card that only showed
recorded results would make an unscanned device look clean, and the device page
is exactly where somebody decides whether a box is fit to leave in service.
"""

from django.conf import settings
from netbox.plugins import PluginTemplateExtension

from netbox_compliance.choices import ConfigCheckResultChoices, ConfigComplianceStatusChoices
from netbox_compliance.models import ConfigCompliance
from netbox_compliance.scoping import StandardResolver

PLUGIN_SETTINGS = settings.PLUGINS_CONFIG.get('netbox_compliance', {})


class DeviceComplianceCard(PluginTemplateExtension):
    models = ['dcim.device']

    def _rows(self, device):
        if device is None:
            return []
        standards = StandardResolver().for_device(device)
        if not standards:
            return []
        records = {
            record.standard_id: record
            for record in ConfigCompliance.objects.filter(
                device=device, standard__in=standards
            ).select_related('standard')
        }
        rows = []
        for standard in standards:
            record = records.get(standard.pk)
            status = record.status if record else ConfigCheckResultChoices.RESULT_UNKNOWN
            rows.append({
                'standard': standard,
                'record': record,
                'status': status,
                'label': _label(status),
                'color': ConfigComplianceStatusChoices.colors.get(status),
                'findings': record.finding_count if record else 0,
                'is_stale': record.is_stale if record else False,
            })
        return rows

    def _render(self):
        device = self.context.get('object')
        return self.render(
            'netbox_compliance/inc/device_compliance_card.html',
            extra_context={'compliance_rows': self._rows(device)},
        )

    def right_page(self):
        if PLUGIN_SETTINGS.get('compliance_card_position', 'right_page') == 'right_page':
            return self._render()
        return ''

    def left_page(self):
        if PLUGIN_SETTINGS.get('compliance_card_position') == 'left_page':
            return self._render()
        return ''


def _label(status):
    labels = {entry[0]: entry[1] for entry in ConfigComplianceStatusChoices.CHOICES}
    return labels.get(status, status)


template_extensions = [DeviceComplianceCard]
