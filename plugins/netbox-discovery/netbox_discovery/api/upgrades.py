"""The scheduler and remote worker API; no arbitrary commands are accepted."""
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from netbox.api.serializers import NetBoxModelSerializer
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import upgrade_queue as queue
from ..models import DiscoveryPoller, UpgradeJob
from ..upgrade_choices import UpgradeOperationChoices
from ..upgrade_filtersets import UpgradeJobFilterSet


class UpgradeJobSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:upgradejob-detail')
    heartbeat_stale = serializers.BooleanField(read_only=True)
    needs_recovery = serializers.BooleanField(read_only=True)

    class Meta:
        model = UpgradeJob
        fields = ('id', 'url', 'display', 'batch_id', 'device', 'device_name', 'poller', 'address',
                  'profile', 'operation', 'scheduled_at', 'start_before', 'status', 'stage', 'message',
                  'summary', 'events', 'sequence', 'run_id', 'requested_by', 'claimed_at', 'started_at',
                  'completed_at', 'last_seen_at', 'heartbeat_stale', 'needs_recovery',
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
        poller.touch(summary='Upgrade worker checked in')
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
