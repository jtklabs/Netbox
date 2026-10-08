from django.db.models import Count
from django.utils import timezone
from netbox.api.serializers import NetBoxModelSerializer
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import serializers

from ..models import AuditSchedule, AuditRun
from ..audit_views import AuditScheduleFilterSet, AuditRunFilterSet
from ..audit_scheduling import payload
from .. import upgrade_queue as queue


class AuditScheduleSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:auditschedule-detail')
    overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = AuditSchedule
        fields = ('id', 'url', 'display', 'name', 'enabled', 'frequency', 'weekday', 'local_time', 'time_zone',
                  'window_hours', 'filters', 'profile_source', 'saved_profile', 'profile', 'run_as',
                  'next_run_at', 'last_run_at', 'overdue', 'description', 'comments', 'tags', 'custom_fields',
                  'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'name', 'enabled')
        read_only_fields = ('run_as', 'next_run_at', 'last_run_at')

    def validate(self, attrs):
        attrs = super().validate(attrs)
        user = self.context['request'].user
        if not user.has_perm('netbox_discovery.add_upgradejob'):
            raise serializers.ValidationError('Scheduling audits requires permission to add upgrade jobs.')
        candidate = AuditSchedule()
        for field in ('name', 'enabled', 'filters', 'profile_source', 'saved_profile', 'profile', 'window_hours'):
            setattr(candidate, field, attrs.get(field, getattr(self.instance or candidate, field)))
        if candidate.enabled:
            try:
                queue.prepare(user, payload(candidate, timezone.now(), user))
            except queue.QueueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return attrs

    def create(self, validated_data):
        validated_data['run_as'] = self.context['request'].user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data['run_as'] = self.context['request'].user
        return super().update(instance, validated_data)


class AuditScheduleViewSet(NetBoxModelViewSet):
    queryset = AuditSchedule.objects.select_related('run_as', 'saved_profile')
    serializer_class = AuditScheduleSerializer
    filterset_class = AuditScheduleFilterSet


class AuditRunSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:auditrun-detail')
    job_status_counts = serializers.SerializerMethodField()

    class Meta:
        model = AuditRun
        fields = ('id', 'url', 'display', 'schedule', 'scheduled_for', 'dispatched_at', 'outcome', 'message',
                  'job_count', 'job_status_counts', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'outcome')
        read_only_fields = fields

    def get_job_status_counts(self, instance):
        jobs = instance.jobs.restrict(self.context['request'].user, 'view')
        return dict(jobs.values('status').annotate(count=Count('pk')).values_list('status', 'count').order_by('status'))


class AuditRunViewSet(NetBoxModelViewSet):
    queryset = AuditRun.objects.select_related('schedule')
    serializer_class = AuditRunSerializer
    filterset_class = AuditRunFilterSet
    http_method_names = ['get', 'head', 'options']
