"""The poller-facing API.

Three calls make the whole flow work:

    POST pollers/check-in/                  "I am awake — what have you got?"
    POST onboarding-requests/{id}/scanned/  "here is what I found"
    POST onboarding-requests/{id}/applied/  "here is the device I created"

Pollers pull. They are at remote sites behind outbound-only firewalls, so
nothing here ever initiates a connection to them.
"""

from django.db import transaction
from django.utils import timezone
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from dcim.models import Device
from netbox_discovery import actions, filtersets
from netbox_discovery.api.serializers import (
    ApplyResultSerializer,
    ApproveSerializer,
    DiscoveryPollerSerializer,
    JobSerializer,
    HardwareReplacementSerializer,
    OnboardingRequestSerializer,
    PollerCheckInSerializer,
    RejectSerializer,
    ScanResultSerializer,
)
from netbox_discovery.choices import OnboardingStatusChoices
from netbox_discovery.models import (
    DiscoveryPoller,
    HardwareReplacement,
    OnboardingRequest,
)


class DiscoveryPollerViewSet(NetBoxModelViewSet):
    queryset = DiscoveryPoller.objects.prefetch_related('tags')
    serializer_class = DiscoveryPollerSerializer
    filterset_class = filtersets.DiscoveryPollerFilterSet

    @action(detail=False, methods=['post'], url_path='check-in')
    def check_in(self, request):
        """Record a poller as alive and hand it the work waiting for it.

        The check-in both claims and returns work in one round trip, because
        the alternative — list, then claim each — races with itself when a
        cron overlaps its previous run.

        An unknown poller name creates the record rather than erroring. There
        is no registration step by design: standing up a poller should be
        installing the scanner and tagging some sites, not remembering to add
        a row in the UI first.
        """
        if not request.user.has_perm('netbox_discovery.change_onboardingrequest'):
            return Response(
                {'detail': 'This token needs change permission on onboarding requests.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PollerCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        poller, _created = DiscoveryPoller.objects.get_or_create(name=data['name'])
        poller.touch(version=data['version'], summary=data['summary'])

        jobs = self._take_work(poller, claim=data['claim'], limit=data['limit'])
        return Response({
            'poller': DiscoveryPollerSerializer(
                poller, context={'request': request}, nested=True
            ).data,
            'jobs': JobSerializer(jobs, many=True).data,
        })

    def _take_work(self, poller, claim=True, limit=25):
        """Claim this poller's outstanding requests and describe them as jobs.

        Claiming is done under a row lock so two overlapping check-ins from the
        same poller cannot both take the same request and scan the device
        twice.
        """
        jobs = []
        with transaction.atomic():
            # `of=('self',)` locks only the onboarding_request rows. Without it
            # the select_related below turns into a LEFT OUTER JOIN across
            # nullable foreign keys, and Postgres refuses: "FOR UPDATE cannot
            # be applied to the nullable side of an outer join".
            queryset = (
                OnboardingRequest.objects
                .select_for_update(of=('self',), skip_locked=True)
                .filter(poller=poller)
                .select_related('site', 'override_site', 'role', 'tenant')
                .order_by('created')
            )
            for entry in queryset[: limit * 4]:
                action_name = self._action_for(entry)
                if action_name is None:
                    continue
                if claim and action_name == 'scan':
                    entry.status = OnboardingStatusChoices.STATUS_SCANNING
                    entry.claimed_at = timezone.now()
                    entry.save()
                site = entry.target_site
                jobs.append({
                    'id': entry.pk,
                    'address': entry.address,
                    'action': action_name,
                    'site': site.pk if site else None,
                    'site_name': site.name if site else '',
                    'override_name': entry.override_name,
                    'role': entry.role.slug if entry.role else '',
                    # Carried so the created device is filed against the right
                    # company — the tenant is why this address was resolvable
                    # at all when the space overlaps.
                    'tenant': entry.tenant_id,
                    'tenant_name': entry.tenant.name if entry.tenant else '',
                })
                if len(jobs) >= limit:
                    break
        return jobs

    @staticmethod
    def _action_for(entry):
        if entry.status in OnboardingStatusChoices.CLAIMABLE_FOR_SCAN:
            return 'scan'
        if entry.status in OnboardingStatusChoices.CLAIMABLE_FOR_APPLY:
            return 'apply'
        # A request whose poller died mid-scan is offered again rather than
        # being left stuck in `scanning` with nothing to move it on.
        if entry.claim_expired:
            return 'scan'
        return None


class OnboardingRequestViewSet(NetBoxModelViewSet):
    queryset = OnboardingRequest.objects.select_related(
        'site', 'override_site', 'prefix', 'poller', 'role', 'device', 'tenant', 'vrf'
    ).prefetch_related('tags')
    serializer_class = OnboardingRequestSerializer
    filterset_class = filtersets.OnboardingRequestFilterSet

    # --- The human half of the workflow, over the API.
    #
    # These mirror the UI buttons exactly, because both call the same functions
    # in actions.py. Anything automating against this gets the same answers a
    # person clicking would, including the refusals.

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Accept a scanned request. The poller applies it on its next check-in.

        An empty body means "as scanned"; send override_name, override_site or
        role to change any of them first, exactly as the review form does.
        """
        entry = self.get_object()
        if not request.user.has_perm('netbox_discovery.change_onboardingrequest'):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            actions.approve(
                entry, user=request.user,
                override_name=data.get('override_name'),
                override_site=data.get('override_site'),
                role=data.get('role'),
            )
        except actions.TransitionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            OnboardingRequestSerializer(entry, context={'request': request}).data
        )

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Decline a request. Nothing is written to DCIM, then or later."""
        entry = self.get_object()
        if not request.user.has_perm('netbox_discovery.change_onboardingrequest'):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            actions.reject(entry, user=request.user,
                           reason=serializer.validated_data['reason'])
        except actions.TransitionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            OnboardingRequestSerializer(entry, context={'request': request}).data
        )

    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        """Queue a failed or unresolvable request again, re-running resolution.

        The fix is nearly always in IPAM — the prefix now exists, or the site
        has been tagged — so this picks that up rather than making a caller
        recreate the request.
        """
        entry = self.get_object()
        if not request.user.has_perm('netbox_discovery.change_onboardingrequest'):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            actions.retry(entry)
        except actions.TransitionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(
            OnboardingRequestSerializer(entry, context={'request': request}).data
        )

    @action(detail=True, methods=['post'])
    def scanned(self, request, pk=None):
        """A poller reporting what it found. Writes nothing to DCIM.

        The request moves to `review` and waits for a person. On failure it
        moves to `failed` with the reason, which is the other thing an operator
        needs to see — an unreachable address and a rejected credential look
        identical from the form otherwise.
        """
        entry = self.get_object()
        if not request.user.has_perm('netbox_discovery.change_onboardingrequest'):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ScanResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.validated_data

        entry.scanned_at = timezone.now()
        if result['ok']:
            entry.discovered = {
                'sys_name': result['sys_name'],
                'sys_descr': result['sys_descr'],
                'credential': result['credential'],
                'devices': result['devices'],
                'access_points': result['access_points'],
            }
            entry.error = ''
            entry.status = OnboardingStatusChoices.STATUS_REVIEW
        else:
            entry.error = result['error']
            entry.status = OnboardingStatusChoices.STATUS_FAILED
        entry.save()
        return Response(
            OnboardingRequestSerializer(entry, context={'request': request}).data
        )

    @action(detail=True, methods=['post'])
    def applied(self, request, pk=None):
        """A poller reporting the outcome of applying an approved request."""
        entry = self.get_object()
        if not request.user.has_perm('netbox_discovery.change_onboardingrequest'):
            return Response({'detail': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = ApplyResultSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.validated_data

        if result['ok']:
            device = Device.objects.filter(pk=result['device']).first()
            if device is None:
                return Response(
                    {'detail': 'No device with id %s.' % result['device']},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            entry.device = device
            entry.status = OnboardingStatusChoices.STATUS_APPLIED
            entry.applied_at = timezone.now()
            entry.error = ''
        else:
            # Back to review rather than failed: the operator already approved
            # this one, so the useful next step is to look at why it would not
            # apply, not to start again.
            entry.status = OnboardingStatusChoices.STATUS_REVIEW
            entry.error = result['error']
        entry.save()
        return Response(
            OnboardingRequestSerializer(entry, context={'request': request}).data
        )


class HardwareReplacementViewSet(NetBoxModelViewSet):
    """Every serial that changed under a name we already knew.

    Writable: the poller creates these when a rescan finds different metal.
    """

    queryset = HardwareReplacement.objects.select_related(
        'device', 'replaced_device', 'poller'
    ).prefetch_related('tags')
    serializer_class = HardwareReplacementSerializer
    filterset_class = filtersets.HardwareReplacementFilterSet
