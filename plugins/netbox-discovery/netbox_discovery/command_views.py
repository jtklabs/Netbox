"""Device Configuration State: the collected show commands, viewable and downloadable."""
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse
from django.shortcuts import get_object_or_404, render
from django.views import View
from dcim.models import Device

from .models import CommandOutput


class _OutputView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.view_commandoutput'
    raise_exception = True

    def output(self, request, pk):
        output = get_object_or_404(CommandOutput.objects.select_related('device'), pk=pk)
        if not Device.objects.restrict(request.user, 'view').filter(pk=output.device_id).exists():
            raise PermissionDenied
        return output


class CommandOutputDownloadView(_OutputView):
    def get(self, request, pk):
        output = self.output(request, pk)
        response = FileResponse(output.file.open('rb'), content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{output.filename}"'
        return response


class CommandOutputRawView(_OutputView):
    def get(self, request, pk):
        output = self.output(request, pk)
        with output.file.open('rb') as handle:
            text = handle.read().decode('utf-8', errors='replace')
        return render(request, 'netbox_discovery/commandoutput.html', {'output': output, 'text': text})


class DeviceCommandOutputsView(PermissionRequiredMixin, View):
    permission_required = 'netbox_discovery.view_commandoutput'
    raise_exception = True

    def get(self, request, pk):
        device = get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)
        outputs = CommandOutput.objects.filter(device=device).select_related('poller')
        return render(request, 'netbox_discovery/device_command_outputs.html', {'device': device, 'outputs': outputs})
