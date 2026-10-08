"""A configuration-compliance card on the device page.

The card lists every standard in force that the device is in scope for, and the
verdict against each — including standards nobody has checked it against, which
render as Not checked rather than being left out. A card that only showed
recorded results would make an unscanned device look clean, and the device page
is exactly where somebody decides whether a box is fit to leave in service.
"""

from django.conf import settings
from netbox.plugins import PluginTemplateExtension

from netbox_compliance.models import ConfigStandard
from netbox_compliance.scoping import device_standard_rows

PLUGIN_SETTINGS = settings.PLUGINS_CONFIG.get('netbox_compliance', {})


class DeviceComplianceCard(PluginTemplateExtension):
    models = ['dcim.device']

    def _rows(self, device):
        if device is None:
            return []
        from netbox_compliance.scoping import active_standards
        request = self.context.get('request')
        user = request.user if request is not None else None
        standards = ConfigStandard.objects.all()
        if user is not None:
            standards = standards.restrict(user, 'view')
        rows = device_standard_rows([device], standards=list(active_standards(queryset=standards).prefetch_related(
            'platforms', 'roles', 'sites', 'device_tags')), user=user)
        for row in rows:
            row.update(label=row['status_label'], color=row['status_color'])
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


template_extensions = [DeviceComplianceCard]
