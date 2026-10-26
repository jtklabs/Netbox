"""Device account preferences; values remain in each worker's vault deployment."""
from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from dcim.models import Device
from .models import DeviceCredentialState


class CredentialForm(forms.Form):
    transport = forms.ChoiceField(choices=(('ssh', 'SSH'), ('https', 'HTTPS')))
    assigned_profile = forms.RegexField(r'^[a-z][a-z0-9_]{0,31}$', required=False,
                                       help_text='Leave blank to discover automatically. Enter the name deployed in the worker environment.')
    allow_fallback = forms.BooleanField(required=False, help_text='Allow other profiles after an assigned account is rejected.')
    forget_selection = forms.BooleanField(required=False, help_text='Forget the previously discovered profile. Does not bypass an active cooldown.')


class DeviceCredentialEditView(LoginRequiredMixin, View):
    def get(self, request, pk):
        device = get_object_or_404(Device.objects.restrict(request.user, 'change'), pk=pk)
        transport = request.GET.get('transport', 'ssh')
        state = DeviceCredentialState.objects.filter(device=device, transport=transport).first()
        form = CredentialForm(initial={'transport': transport, 'assigned_profile': state.assigned_profile if state else '',
                                       'allow_fallback': state.allow_fallback if state else False})
        return render(request, 'netbox_discovery/device_credentials.html', {'object': device, 'form': form})

    @transaction.atomic
    def post(self, request, pk):
        device = get_object_or_404(Device.objects.restrict(request.user, 'change').select_for_update(), pk=pk)
        form = CredentialForm(request.POST)
        if form.is_valid():
            state, _ = DeviceCredentialState.objects.get_or_create(device=device, transport=form.cleaned_data['transport'])
            if state.lease_until and state.lease_until > timezone.now():
                form.add_error(None, 'Credential verification is running. Retry after it finishes.')
            else:
                state.assigned_profile = form.cleaned_data['assigned_profile']
                state.allow_fallback = form.cleaned_data['allow_fallback']
                if form.cleaned_data['forget_selection']:
                    state.selected_profile, state.verified_at, state.status = '', None, 'unknown'
                state.save()
                messages.success(request, 'Credential preferences saved.')
                return redirect(device.get_absolute_url())
        return render(request, 'netbox_discovery/device_credentials.html', {'object': device, 'form': form})
