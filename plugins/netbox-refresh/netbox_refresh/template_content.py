from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from netbox.plugins import PluginTemplateExtension

from netbox_refresh.models import DeviceSoftware, ModelLifecycle, SoftwareStandard

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


class DeviceSoftwareCard(PluginTemplateExtension):
    """Running software and compliance, on the device page itself.

    This is where most people will actually meet the feature — nobody opens a
    compliance report to ask about one device. An exempt device says so here
    rather than showing nothing.
    """

    models = ['dcim.device']

    def full_width_page(self):
        device = self.context.get('object')
        if device is None:
            return ''
        record = DeviceSoftware.objects.filter(device=device).select_related(
            'software_version', 'software_version__platform'
        ).first()
        return self.render(
            'netbox_refresh/inc/device_software_card.html',
            extra_context={'software': record, 'device': device},
        )


class BaseStandardCard(PluginTemplateExtension):
    """The software standard in force for whatever this page is about."""

    def _scope(self):
        return self.context.get('object')

    def _standard_for(self, obj):
        if obj is None:
            return None
        from netbox_refresh.compliance import active_standards

        content_type = ContentType.objects.get_for_model(obj)
        return active_standards().filter(
            assigned_object_type=content_type, assigned_object_id=obj.pk
        ).prefetch_related('approved_versions').first()

    def right_page(self):
        scope = self._scope()
        return self.render(
            'netbox_refresh/inc/software_standard_card.html',
            extra_context={'standard': self._standard_for(scope), 'scope': scope},
        )


class DeviceTypeStandardCard(BaseStandardCard):
    models = ['dcim.devicetype']


class PlatformStandardCard(BaseStandardCard):
    models = ['dcim.platform']


template_extensions = (
    DeviceTypeLifecycleCard,
    ModuleTypeLifecycleCard,
    DeviceLifecycleCard,
    ModuleLifecycleCard,
    DeviceSoftwareCard,
    DeviceTypeStandardCard,
    PlatformStandardCard,
)
