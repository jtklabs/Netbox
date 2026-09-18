"""The scheduler and remote worker API; no arbitrary commands are accepted."""
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from netbox.api.serializers import NetBoxModelSerializer
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from dcim.api.serializers import DeviceSerializer
from dcim.models import Device
from netbox.api.fields import SerializedPKRelatedField

from .. import upgrade_groups
from .. import upgrade_queue as queue
from ..models import DiscoveryPoller, UpgradeDependency, UpgradeGroup, UpgradeJob
from ..upgrade_choices import UpgradeOperationChoices
from ..upgrade_filtersets import UpgradeDependencyFilterSet, UpgradeGroupFilterSet, UpgradeJobFilterSet


class UpgradeJobSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:upgradejob-detail')
    heartbeat_stale = serializers.BooleanField(read_only=True)
    needs_recovery = serializers.BooleanField(read_only=True)
    poller_last_seen_at = serializers.DateTimeField(source='poller.upgrade_last_seen_at', read_only=True)

    class Meta:
        model = UpgradeJob
        fields = ('id', 'url', 'display', 'batch_id', 'device', 'device_name', 'poller', 'address',
                  'profile', 'operation', 'scheduled_at', 'start_before', 'status', 'stage', 'message',
                  'summary', 'events', 'sequence', 'run_id', 'requested_by', 'claimed_at', 'started_at',
                  'completed_at', 'last_seen_at', 'poller_last_seen_at', 'heartbeat_stale', 'needs_recovery',
                  'groups', 'waits_for', 'held_reason', 'planned_wave', 'acknowledged',
                  'description', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'status', 'stage')
        read_only_fields = fields


class UpgradeJobViewSet(NetBoxModelViewSet):
    queryset = UpgradeJob.objects.select_related('device', 'poller')
    serializer_class = UpgradeJobSerializer
    filterset_class = UpgradeJobFilterSet
    http_method_names = ['get', 'head', 'options']


class ScheduleSerializer(serializers.Serializer):
    filters = serializers.JSONField()
    profile = serializers.JSONField()
    operation = serializers.ChoiceField(choices=UpgradeOperationChoices, default='audit')
    scheduled_at = serializers.DateTimeField()
    start_before = serializers.DateTimeField()
    poller = serializers.RegexField(r'^(?:poller-)?[a-z0-9][a-z0-9_-]*$', required=False, allow_blank=True)
    description = serializers.CharField(max_length=200, default='', allow_blank=True)
    preview = serializers.BooleanField(default=False)


class CheckInSerializer(serializers.Serializer):
    name = serializers.RegexField(r'^(?:poller-)?[a-z0-9][a-z0-9_-]*$', max_length=100)
    limit = serializers.IntegerField(min_value=1, max_value=20, default=3)
    apply = serializers.BooleanField(default=False)


class ReportSerializer(serializers.Serializer):
    claim_token = serializers.UUIDField()
    heartbeat = serializers.BooleanField(default=False)
    sequence = serializers.IntegerField(min_value=1, max_value=2147483647, required=False)
    stage = serializers.RegexField(r'^[a-z_]+$', max_length=50, required=False)
    message = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    run_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    summary = serializers.JSONField(required=False)

    def validate(self, data):
        if not data['heartbeat'] and not all(k in data for k in ('sequence', 'stage', 'message')):
            raise serializers.ValidationError('sequence, stage and message are required for events.')
        if 'summary' in data:
            import json
            if not isinstance(data['summary'], dict) or len(json.dumps(data['summary'])) > 64000:
                raise serializers.ValidationError('summary must be an object under 64KB; keep raw snapshots in the local archive.')
        return data


class QueueView(APIView):
    permission_classes = [IsAuthenticated]
    permission = ''
    serializer_class = None

    def post(self, request, **kwargs):
        from users.models import Token
        if isinstance(request.auth, Token) and not request.auth.write_enabled:
            raise PermissionDenied('This token is read-only.')
        if not request.user.has_perm('netbox_discovery.' + self.permission):
            raise PermissionDenied()
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return self.execute(request, serializer.validated_data, **kwargs)
        except (queue.QueueError, ValidationError) as exc:
            return Response({'detail': str(exc)}, status=409)
        except ObjectDoesNotExist:
            return Response({'detail': 'Job does not exist or is outside this token’s permissions.'}, status=404)


class ScheduleView(QueueView):
    permission = 'add_upgradejob'
    serializer_class = ScheduleSerializer

    def execute(self, request, data):
        if data['preview']:
            rows = queue.prepare(request.user, data)
            return Response({'devices': [{'id': row['device'].pk, 'name': row['device'].name,
                                         'address': row['address'], 'poller': row['poller_name']} for row in rows]})
        jobs = queue.schedule(request.user, data)
        return Response({'batch_id': str(jobs[0].batch_id),
                         'jobs': UpgradeJobSerializer(jobs, many=True, context={'request': request}).data}, status=201)


class UpgradeCheckInView(QueueView):
    permission = 'run_upgradejob'
    serializer_class = CheckInSerializer

    def execute(self, request, data):
        name = data['name'].removeprefix('poller-')
        poller, _ = DiscoveryPoller.objects.get_or_create(name=name)
        poller.touch(summary='Upgrade worker checked in', upgrade=True)
        jobs = queue.claim(request.user, poller, data['limit'], data['apply'])
        return Response({'jobs': [queue.assignment(job) for job in jobs]})


class UpgradeReportView(QueueView):
    permission = 'run_upgradejob'
    serializer_class = ReportSerializer

    def execute(self, request, data, pk):
        job = queue.report(request.user, pk, data)
        return Response({'id': job.pk, 'status': job.status, 'sequence': job.sequence})


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, default='', allow_blank=True)
    recovered = serializers.BooleanField(default=False)


class UpgradeCancelView(QueueView):
    permission = 'change_upgradejob'
    serializer_class = CancelSerializer

    def execute(self, request, data, pk):
        job = queue.cancel(request.user, pk, **data)
        return Response({'id': job.pk, 'status': job.status})


class UpgradeRequeueView(QueueView):
    permission = 'add_upgradejob'
    serializer_class = serializers.Serializer

    def execute(self, request, data, pk):
        job = queue.requeue(request.user, pk)
        return Response({'id': job.pk, 'batch_id': job.batch_id, 'status': job.status,
                         'scheduled_at': job.scheduled_at, 'start_before': job.start_before}, status=201)


class UpgradeGroupSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:upgradegroup-detail')
    members = SerializedPKRelatedField(queryset=Device.objects.all(), serializer=DeviceSerializer, nested=True,
                                       required=False, many=True)
    depends_on = serializers.PrimaryKeyRelatedField(queryset=UpgradeGroup.objects.all(), many=True, required=False)

    def validate_depends_on(self, value):
        if self.instance is not None and self.instance in value:
            raise serializers.ValidationError('A group cannot wait for itself.')
        return value

    class Meta:
        model = UpgradeGroup
        fields = ('id', 'url', 'display', 'name', 'max_concurrent', 'source', 'key', 'stale', 'members', 'depends_on',
                  'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'name', 'max_concurrent')
        read_only_fields = ('source', 'key', 'stale')


class UpgradeGroupViewSet(NetBoxModelViewSet):
    queryset = UpgradeGroup.objects.prefetch_related('members', 'tags')
    serializer_class = UpgradeGroupSerializer
    filterset_class = UpgradeGroupFilterSet


class UpgradeDependencySerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:upgradedependency-detail')
    upstream = DeviceSerializer(nested=True)
    downstream = DeviceSerializer(nested=True)

    class Meta:
        model = UpgradeDependency
        fields = ('id', 'url', 'display', 'upstream', 'downstream', 'source', 'key', 'stale',
                  'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'upstream', 'downstream')
        read_only_fields = ('source', 'key', 'stale')


class UpgradeDependencyViewSet(NetBoxModelViewSet):
    queryset = UpgradeDependency.objects.select_related('upstream', 'downstream').prefetch_related('tags')
    serializer_class = UpgradeDependencySerializer
    filterset_class = UpgradeDependencyFilterSet


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)


class UpgradeHoldView(QueueView):
    permission = 'change_upgradejob'
    serializer_class = ReasonSerializer

    def execute(self, request, data, pk):
        job = queue.hold(request.user, pk, data['reason'])
        return Response({'id': job.pk, 'status': job.status, 'held_reason': job.held_reason})


class UpgradeReleaseView(QueueView):
    permission = 'change_upgradejob'
    serializer_class = ReasonSerializer

    def execute(self, request, data, pk):
        job = queue.release(request.user, pk, data['reason'])
        return Response({'id': job.pk, 'status': job.status})


class ReleaseBatchSerializer(ReasonSerializer):
    batch_id = serializers.UUIDField()


class UpgradeReleaseBatchView(QueueView):
    permission = 'change_upgradejob'
    serializer_class = ReleaseBatchSerializer

    def execute(self, request, data):
        return Response({'released': queue.release_batch(request.user, data['batch_id'], data['reason'])})


class UpgradeRefreshGroupsView(QueueView):
    permission = 'change_upgradegroup'
    serializer_class = serializers.Serializer

    def execute(self, request, data):
        return Response(upgrade_groups.refresh_discovered())
