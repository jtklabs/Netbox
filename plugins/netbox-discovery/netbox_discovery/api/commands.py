"""Poller upload and read-only listing of show-command outputs."""
import django_filters
from dcim.models import Device
from django.urls import reverse
from netbox.api.pagination import OptionalLimitOffsetPagination
from rest_framework import mixins, serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ..models import CommandOutput, DiscoveryPoller
from ..resolution import normalise_poller_name
from ..utils import plugin_setting


class CommandOutputSerializer(serializers.ModelSerializer):
    device = serializers.SerializerMethodField()
    poller = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = CommandOutput
        fields = ('id', 'device', 'poller', 'platform', 'command', 'filename', 'ok', 'error', 'size', 'sha256',
                  'collected_at', 'updated', 'download_url')

    def get_device(self, obj):
        return {'id': obj.device_id, 'name': obj.device.name}

    def get_poller(self, obj):
        return obj.poller.name if obj.poller_id else None

    def get_download_url(self, obj):
        request = self.context.get('request')
        path = reverse('plugins:netbox_discovery:commandoutput_download', args=[obj.pk])
        return request.build_absolute_uri(path) if request else path


class CommandOutputFilterSet(django_filters.FilterSet):
    device_id = django_filters.NumberFilter(field_name='device_id')
    device = django_filters.CharFilter(field_name='device__name')
    poller = django_filters.CharFilter(field_name='poller__name')

    class Meta:
        model = CommandOutput
        fields = ('platform', 'command', 'ok')


class UploadSerializer(serializers.Serializer):
    device = serializers.PrimaryKeyRelatedField(queryset=Device.objects.all())
    poller = serializers.RegexField(r'^(?:poller-)?[a-z0-9][a-z0-9_-]*$', required=False, allow_blank=True)
    platform = serializers.CharField(max_length=50, required=False, allow_blank=True)
    command = serializers.CharField(max_length=200)
    filename = serializers.RegexField(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$')
    ok = serializers.BooleanField(default=True)
    error = serializers.CharField(required=False, allow_blank=True, default='')
    collected_at = serializers.DateTimeField(required=False)
    content = serializers.CharField(allow_blank=True, trim_whitespace=False)

    def validate_content(self, value):
        limit = int(plugin_setting('command_output_max_bytes'))
        if len(value.encode('utf-8')) > limit:
            raise serializers.ValidationError(f'content exceeds {limit} bytes; keep it on the poller and report a note instead')
        return value


class CommandOutputViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    queryset = CommandOutput.objects.select_related('device', 'poller')
    serializer_class = CommandOutputSerializer
    permission_classes = [IsAuthenticated]
    filterset_class = CommandOutputFilterSet
    pagination_class = OptionalLimitOffsetPagination

    def get_queryset(self):
        if not self.request.user.has_perm('netbox_discovery.view_commandoutput'):
            return CommandOutput.objects.none()
        return super().get_queryset()

    def create(self, request):
        """Poller upload: one output per device and command, replaced on every collection."""
        from users.models import Token
        if isinstance(request.auth, Token) and not request.auth.write_enabled:
            raise PermissionDenied('This token is read-only.')
        serializer = UploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        device = data['device']
        if not Device.objects.restrict(request.user, 'view').filter(pk=device.pk).exists():
            raise PermissionDenied('This token cannot see that device.')
        existing = CommandOutput.objects.filter(device=device, command=data['command']).first()
        needed = 'change_commandoutput' if existing else 'add_commandoutput'
        if not request.user.has_perm('netbox_discovery.' + needed):
            raise PermissionDenied(f'This token needs {needed} permission.')
        output = existing or CommandOutput(device=device, command=data['command'])
        if data.get('poller'):
            output.poller, _ = DiscoveryPoller.objects.get_or_create(name=normalise_poller_name(data['poller']))
        output.platform, output.filename = data.get('platform', ''), data['filename']
        output.ok, output.error = data['ok'], data.get('error', '')[:4000]
        output.collected_at = data.get('collected_at') or output.collected_at
        output.store(data['content'])
        output.save()
        return Response(CommandOutputSerializer(output, context={'request': request}).data,
                        status=200 if existing else 201)

