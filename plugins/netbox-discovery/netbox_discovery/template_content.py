"""The Device Configuration State panel on every device page."""
from netbox.plugins import PluginTemplateExtension

from .models import CommandOutput, DeviceTypeProfile


class DeviceCommandOutputs(PluginTemplateExtension):
    models = ['dcim.device']

    def full_width_page(self):
        request = self.context['request']
        if not request.user.has_perm('netbox_discovery.view_commandoutput'):
            return ''
        outputs = list(CommandOutput.objects.filter(device=self.context['object']).select_related('poller'))
        latest = max((o.collected_at for o in outputs), default=None)
        return self.render('netbox_discovery/inc/device_command_outputs.html', extra_context={'outputs': outputs, 'latest': latest})


class DeviceTypeJobProfiles(PluginTemplateExtension):
    models = ['dcim.devicetype']

    def right_page(self):
        request = self.context['request']
        if not request.user.has_perm('netbox_discovery.view_devicetypeprofile'):
            return ''
        assignment = DeviceTypeProfile.objects.restrict(request.user, 'view').filter(
            device_type=self.context['object']).select_related('upgrade_profile', 'remediation_profile').first()
        return self.render('netbox_discovery/inc/model_profiles.html', extra_context={'assignment': assignment})


template_extensions = [DeviceCommandOutputs, DeviceTypeJobProfiles]
