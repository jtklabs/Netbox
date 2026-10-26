"""Serialize login discovery across workers; only non-secret metadata is accepted."""
from datetime import timedelta
import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from dcim.models import Device
from ..models import DeviceCredentialState

NAME = r'^[a-z][a-z0-9_]{0,31}$'


class CredentialRequest(serializers.Serializer):
    action = serializers.ChoiceField(choices=('begin', 'finish'))
    transport = serializers.ChoiceField(choices=('ssh', 'https'))
    profiles = serializers.ListField(child=serializers.RegexField(NAME), min_length=1, max_length=10, required=False)
    max_attempts = serializers.IntegerField(min_value=1, max_value=3, default=3)
    token = serializers.UUIDField(required=False)
    attempted = serializers.ListField(child=serializers.RegexField(NAME), min_length=1, max_length=3, required=False)
    profile = serializers.RegexField(NAME, allow_blank=True, required=False)
    status = serializers.ChoiceField(choices=('authenticated', 'rejected', 'unavailable'), required=False)

    def validate(self, data):
        required = ('profiles',) if data['action'] == 'begin' else ('token', 'attempted', 'profile', 'status')
        if any(key not in data for key in required):
            raise serializers.ValidationError('Missing credential selection fields')
        if data.get('profiles') and len(set(data['profiles'])) != len(data['profiles']):
            raise serializers.ValidationError('Profile names must be unique')
        return data


@transaction.atomic
def exchange(device_id, data):
    # Lock the existing device even on first contact, avoiding a get_or_create race.
    Device.objects.select_for_update().get(pk=device_id)
    state, _ = DeviceCredentialState.objects.get_or_create(device_id=device_id, transport=data['transport'])
    now = timezone.now()
    if data['action'] == 'begin':
        if state.lease_until and state.lease_until > now:
            return {'error': 'Credential verification is already running on another worker; retry later'}
        if state.cooldown_until and state.cooldown_until > now:
            return {'error': 'Credential verification cooldown is active; retry after ' + state.cooldown_until.isoformat()}
        profiles = data['profiles']
        preferred = state.assigned_profile or state.selected_profile
        if preferred and preferred not in profiles:
            return {'error': f'Profile {preferred!r} missing on this poller; deploy it from the vault'}
        # A rebooting device may reject even valid credentials. Never interpret
        # a failed login as permission to replace a saved/assigned identity.
        ordered = [preferred] if preferred else profiles
        state.candidates = ordered[:data['max_attempts']]
        state.lease_token = uuid.uuid4()
        # Crash protection: another worker cannot immediately repeat the attempts.
        state.lease_until = now + timedelta(minutes=10)
        state.status = 'checking'
        state.save()
        return {'token': str(state.lease_token), 'profiles': state.candidates, 'pinned': bool(preferred)}
    if (not state.lease_token or state.lease_token != data['token']
            or not state.lease_until or state.lease_until <= now):
        return {'error': 'Credential verification lease expired or does not match; retry later'}
    attempted = data['attempted']
    if attempted != state.candidates[:len(attempted)]:
        return {'error': 'Credential attempts do not match the reserved order'}
    if data['status'] == 'authenticated' and data['profile'] != attempted[-1]:
        return {'error': 'Successful profile does not match the last attempt'}
    if data['status'] != 'authenticated' and data['profile']:
        return {'error': 'Failed verification cannot select a profile'}
    preferred = state.assigned_profile or state.selected_profile
    if preferred and any(profile != preferred for profile in attempted):
        # Reject results from fallback leases issued before this policy was
        # deployed too. A prior authorization cannot replace a saved identity.
        return {'error': 'Saved credential selection is fixed; request rediscovery explicitly'}
    state.attempted, state.status = attempted, data['status']
    if state.status == 'authenticated':
        state.selected_profile = data['profile']
        state.verified_at = now
        state.cooldown_until = None
    else:
        state.cooldown_until = now + timedelta(minutes=15)
    state.lease_token, state.lease_until, state.candidates = None, None, []
    state.save()
    return {'profile': state.selected_profile, 'status': state.status}


class DeviceCredentialView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from users.models import Token
        if isinstance(request.auth, Token) and not request.auth.write_enabled:
            raise PermissionDenied('This token is read-only.')
        if not (request.user.has_perm('netbox_discovery.run_upgradejob') or request.user.has_perm('dcim.change_device')):
            raise PermissionDenied()
        if not Device.objects.restrict(request.user, 'view').filter(pk=pk).exists():
            raise NotFound()
        serializer = CredentialRequest(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(exchange(pk, serializer.validated_data))
