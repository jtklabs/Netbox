"""REST API.

Two endpoints matter beyond the plain CRUD:

  GET  /config-standards/?device_id=N   the standards in force for one device,
       resolved server-side. The checker asks NetBox rather than reimplementing
       "empty scope means no restriction", so the report and the checker can
       never disagree about what a device is measured against.

  POST /config-compliance/report/       idempotent upsert of results, one item
       per (device, standard). Accepts a list, so a fleet sweep is one call.

The report endpoint takes the same care netbox_refresh's does about the
changelog. ConfigCompliance is a NetBoxModel, so every save() writes an
ObjectChange; a nightly check re-confirming that four hundred devices are still
compliant would produce four hundred changelog entries a night and bury the
handful of rows that actually changed. So an unchanged verdict bumps
last_checked with a queryset update — no signals, no changelog — and only a
changed one goes through save().
"""

from dcim.models import Device
from django.db.models import Count
from django.utils import timezone
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from netbox_compliance import filtersets
from netbox_compliance.api.serializers import (
    ConfigCheckReportSerializer,
    ConfigComplianceSerializer,
    ConfigStandardSerializer,
)
from netbox_compliance.models import ConfigCompliance, ConfigStandard
from netbox_compliance.scoping import active_standards

__all__ = (
    'ConfigStandardViewSet',
    'ConfigComplianceViewSet',
)


class ConfigStandardViewSet(NetBoxModelViewSet):
    # result_count is annotated here as well as in the list view: the
    # serializer declares it, and a declared field that quietly never appears
    # because the queryset forgot to compute it is worse than no field.
    queryset = ConfigStandard.objects.prefetch_related(
        'platforms', 'roles', 'sites', 'device_tags', 'tags'
    ).annotate(result_count=Count('results', distinct=True))
    serializer_class = ConfigStandardSerializer
    filterset_class = filtersets.ConfigStandardFilterSet


class ConfigComplianceViewSet(NetBoxModelViewSet):
    queryset = ConfigCompliance.objects.select_related(
        'device', 'device__site', 'standard'
    ).prefetch_related('tags')
    serializer_class = ConfigComplianceSerializer
    filterset_class = filtersets.ConfigComplianceFilterSet

    @action(detail=False, methods=['post'], url_path='report')
    def report(self, request):
        """Record check results. One item per device per standard; a list is fine.

        Each item is resolved and reported on independently: one unknown device
        in a batch of five hundred does not fail the other four hundred and
        ninety-nine, and the response says which was which.
        """
        if not request.user.has_perm('netbox_compliance.add_configcompliance') or \
                not request.user.has_perm('netbox_compliance.change_configcompliance'):
            return Response(
                {'detail': 'This token needs add and change permission on config compliance.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = request.data
        many = isinstance(payload, list)
        serializer = ConfigCheckReportSerializer(data=payload, many=many)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data if many else [serializer.validated_data]

        results = [self._apply(item) for item in items]
        summary = {}
        for entry in results:
            summary[entry['result']] = summary.get(entry['result'], 0) + 1

        return Response({'summary': summary, 'results': results}, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------ #
    def _apply(self, item):
        device = self._resolve_device(item)
        if device is None:
            return {
                'device': item.get('device') or item.get('device_id'),
                'standard': item.get('standard') or item.get('standard_id'),
                'result': 'error',
                'detail': 'No such device in NetBox, or the name matches more than one.',
            }

        standard = self._resolve_standard(item)
        if standard is None:
            return {
                'device': device.name or device.pk,
                'standard': item.get('standard') or item.get('standard_id'),
                'result': 'error',
                'detail': 'No standard by that name is in force.',
            }

        now = timezone.now()
        checked_at = item.get('checked_at') or now
        findings = item.get('findings') or {}
        observed = item.get('observed') or ''
        error_message = item.get('error_message') or ''

        record = ConfigCompliance.objects.filter(device=device, standard=standard).first()
        created = record is None
        if created:
            record = ConfigCompliance(device=device, standard=standard)

        remediated = item.get('remediated')
        unchanged = (
            not created
            and not remediated
            and record.result == item['result']
            and (record.findings or {}) == findings
            and record.observed == observed
            and record.error_message == error_message
        )

        if unchanged:
            # Nothing about the device changed; we only confirmed it again. A
            # queryset update bypasses signals, so no ObjectChange is written.
            ConfigCompliance.objects.filter(pk=record.pk).update(
                last_checked=checked_at, source=item['source']
            )
            return {
                'device': device.name or device.pk,
                'standard': standard.name,
                'id': record.pk,
                'status': item['result'],
                'result': 'unchanged',
            }

        previous = None if created else record.result
        record.result = item['result']
        record.findings = findings
        record.observed = observed
        record.error_message = error_message
        record.source = item['source']
        record.last_checked = checked_at

        if remediated:
            # Only a run that actually wrote anything sends these, and it sends
            # the pre-change capture it took *before* writing. Overwriting the
            # previous capture is fine: PrimaryModel snapshots the old value
            # into the ObjectChange, so the history is in the changelog.
            record.pre_change_config = item.get('pre_change_config') or ''
            record.pre_change_at = checked_at
            record.remediation_log = item.get('remediation_log') or ''
            record.last_remediated = now

        record.save()

        outcome = 'created' if created else 'updated'
        if previous is not None and previous != record.result:
            outcome = 'changed'
        return {
            'device': device.name or device.pk,
            'standard': standard.name,
            'id': record.pk,
            'status': record.result,
            'previous': previous,
            'result': outcome,
        }

    def _resolve_device(self, item):
        if item.get('device_id'):
            return Device.objects.filter(pk=item['device_id']).first()
        name = (item.get('device') or '').strip()
        if not name:
            return None
        # Device names are not unique across sites in NetBox, so an ambiguous
        # name is unresolvable rather than a coin toss — recording a result
        # against the wrong switch is worse than recording none.
        matches = list(Device.objects.filter(name__iexact=name)[:2])
        return matches[0] if len(matches) == 1 else None

    def _resolve_standard(self, item):
        if item.get('standard_id'):
            return ConfigStandard.objects.filter(pk=item['standard_id']).first()
        name = (item.get('standard') or '').strip()
        if not name:
            return None
        # Standards are effective-dated and superseded by opening a new version
        # under the same name, so a name alone means "the one in force now".
        return active_standards(
            queryset=ConfigStandard.objects.filter(name__iexact=name)
        ).first()
