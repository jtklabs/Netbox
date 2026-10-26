"""The Device Configuration State panel on every device page."""
from netbox.plugins import PluginTemplateExtension

from .models import CommandOutput, DeviceTypeProfile, PlatformProfile


class DeviceRunStandards(PluginTemplateExtension):
    models = ['dcim.device']

    def right_page(self):
        from django.apps import apps
        from django.core.exceptions import ValidationError
        from . import upgrade_queue as queue
        if not apps.is_installed('netbox_compliance'):
            return ''
        user = self.context['request'].user
        if not user.has_perm('netbox_discovery.add_upgradejob'):
            return ''
        from dcim.models import Device
        from .quick_run import choices_for
        device = self.context['object']
        try:
            targets = queue.execution_devices(user, Device.objects.restrict(user, 'view').filter(pk=device.pk))
            choices = choices_for(user, targets[0]) if targets else []
            error = ''
        except (queue.QueueError, ValidationError) as exc:
            choices, error = [], str(exc)
        return self.render('netbox_discovery/inc/device_run_standards.html', extra_context={
            'run_choices': choices, 'run_error': error,
            'can_apply_standards': user.has_perm('netbox_discovery.apply_upgradejob'),
        })


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


class PlatformJobProfiles(PluginTemplateExtension):
    models = ['dcim.platform']

    def right_page(self):
        request = self.context['request']
        if not request.user.has_perm('netbox_discovery.view_platformprofile'):
            return ''
        assignment = PlatformProfile.objects.restrict(request.user, 'view').filter(
            platform=self.context['object']).select_related('remediation_profile').first()
        return self.render('netbox_discovery/inc/platform_profiles.html', extra_context={'assignment': assignment})


class DeviceUCSBackups(PluginTemplateExtension):
    models = ['dcim.device']

    def right_page(self):
        from .models import UpgradeJob
        from .upgrade_backups import backup_context
        user = self.context['request'].user
        if not (user.has_perm('netbox_discovery.view_upgradejob')
                and user.has_perm('netbox_discovery.apply_upgradejob')):
            return ''
        jobs = UpgradeJob.objects.restrict(user, 'view').restrict(user, 'apply').filter(
            device=self.context['object'], summary__has_key='ucs_backup').order_by('-scheduled_at', '-pk')[:10]
        backups = [record for job in jobs if (record := backup_context(job, user))]
        return self.render('netbox_discovery/inc/ucs_backups.html', extra_context={'backups': backups}) if backups else ''


class DeviceCredentials(PluginTemplateExtension):
    models = ['dcim.device']

    def right_page(self):
        from dcim.models import Device
        from .models import DeviceCredentialState
        user, device = self.context['request'].user, self.context['object']
        if not Device.objects.restrict(user, 'view').filter(pk=device.pk).exists():
            return ''
        return self.render('netbox_discovery/inc/device_credentials.html', extra_context={
            'credential_states': DeviceCredentialState.objects.filter(device=device).order_by('transport'),
            'can_change_credentials': Device.objects.restrict(user, 'change').filter(pk=device.pk).exists(),
        })


template_extensions = [DeviceCommandOutputs, DeviceTypeJobProfiles, PlatformJobProfiles, DeviceRunStandards, DeviceUCSBackups, DeviceCredentials]
