from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from netbox.plugins import PluginTemplateExtension

from netbox_refresh.models import ModelLifecycle

PLUGIN_SETTINGS = settings.PLUGINS_CONFIG.get('netbox_refresh', {})


class BaseLifecycleCard(PluginTemplateExtension):
    """Show the lifecycle record for whichever hardware model the page is about."""

    def _lifecycle_for(self, obj):
        if obj is None:
            return None
        content_type = ContentType.objects.get_for_model(obj)
        return ModelLifecycle.objects.filter(
            assigned_object_type=content_type, assigned_object_id=obj.pk
        ).first()

    def _target(self):
        return self.context.get('object')

    def _render(self):
        return self.render(
            'netbox_refresh/inc/lifecycle_card.html',
            extra_context={'lifecycle': self._lifecycle_for(self._target())},
        )

    def right_page(self):
        if PLUGIN_SETTINGS.get('lifecycle_card_position', 'right_page') == 'right_page':
            return self._render()
        return ''

    def left_page(self):
        if PLUGIN_SETTINGS.get('lifecycle_card_position') == 'left_page':
            return self._render()
        return ''


class DeviceTypeLifecycleCard(BaseLifecycleCard):
    models = ['dcim.devicetype']


class ModuleTypeLifecycleCard(BaseLifecycleCard):
    models = ['dcim.moduletype']


class DeviceLifecycleCard(BaseLifecycleCard):
    """On a device page the relevant lifecycle is its device type's."""

    models = ['dcim.device']

    def _target(self):
        device = self.context.get('object')
        return device.device_type if device else None


class ModuleLifecycleCard(BaseLifecycleCard):
    models = ['dcim.module']

    def _target(self):
        module = self.context.get('object')
        return module.module_type if module else None


template_extensions = (
    DeviceTypeLifecycleCard,
    ModuleTypeLifecycleCard,
    DeviceLifecycleCard,
    ModuleLifecycleCard,
)
