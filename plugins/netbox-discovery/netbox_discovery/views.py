from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from netbox.views.generic import (
    BulkDeleteView,
    BulkImportView,
    ObjectDeleteView,
    ObjectEditView,
    ObjectListView,
    ObjectView,
)
from utilities.views import register_model_view

from netbox_discovery import actions, filtersets, forms, tables
from netbox_discovery.choices import OnboardingStatusChoices
from netbox_discovery.models import (
    DiscoveryIssue,
    DiscoveryPoller,
    HardwareReplacement,
    OnboardingRequest,
)
from netbox_discovery.resolution import sites_for_poller

__all__ = (
    'OnboardingRequestListView',
    'OnboardingRequestView',
    'OnboardingRequestEditView',
    'OnboardingRequestDeleteView',
    'OnboardingRequestBulkImportView',
    'OnboardingRequestBulkDeleteView',
    'OnboardingApproveView',
    'OnboardingRejectView',
    'OnboardingRetryView',
    'OnboardingManualEntryView',
    'DiscoveryPollerListView',
    'DiscoveryPollerView',
    'DiscoveryPollerEditView',
    'DiscoveryPollerDeleteView',
    'DiscoveryPollerBulkDeleteView',
    'HardwareReplacementListView',
    'HardwareReplacementView',
    'HardwareReplacementDeleteView',
    'HardwareReplacementBulkDeleteView',
    'DiscoveryIssueListView',
    'DiscoveryIssueView',
    'DiscoveryIssueEditView',
    'DiscoveryIssueDeleteView',
    'DiscoveryIssueBulkDeleteView',
)


@register_model_view(OnboardingRequest, name='list')
class OnboardingRequestListView(ObjectListView):
    queryset = OnboardingRequest.objects.select_related(
        'site', 'override_site', 'poller', 'device', 'requested_by'
    )
    table = tables.OnboardingRequestTable
    filterset = filtersets.OnboardingRequestFilterSet
    filterset_form = forms.OnboardingRequestFilterForm


@register_model_view(OnboardingRequest)
class OnboardingRequestView(ObjectView):
    queryset = OnboardingRequest.objects.select_related(
        'site', 'override_site', 'prefix', 'poller', 'role', 'device'
    )

    def get_extra_context(self, request, instance):
        return {
            'review_form': forms.OnboardingReviewForm(initial={
                'override_name': instance.override_name,
                'override_site': instance.override_site,
                'role': instance.role,
            }),
            'can_review': instance.status == OnboardingStatusChoices.STATUS_REVIEW,
            # Includes review, where it means "scan it again": IPAM is often
            # corrected *after* a scan ran, and the findings on screen are
            # then against the wrong site. The action is the same one
            # actions.retry() has always allowed here — the UI was the only
            # thing refusing, which left the two front doors disagreeing.
            'can_retry': instance.status in (
                OnboardingStatusChoices.STATUS_FAILED,
                OnboardingStatusChoices.STATUS_UNRESOLVED,
                OnboardingStatusChoices.STATUS_REVIEW,
            ),
            # A request awaiting review already holds findings, so queueing it
            # again throws them away. Same action, different thing to warn
            # about, and it must not read like a harmless retry sitting next
            # to Apply and Reject.
            'retry_discards_findings':
                instance.status == OnboardingStatusChoices.STATUS_REVIEW,
            # Offered exactly where it is the answer: the scan failed, so
            # nothing else is going to fill these in.
            'can_enter_manually': instance.status == OnboardingStatusChoices.STATUS_FAILED,
            'manual_form': forms.OnboardingManualEntryForm(initial={
                'name': instance.override_name or instance.address,
                'override_site': instance.override_site,
                'role': instance.role,
            }),
        }


@register_model_view(OnboardingRequest, 'edit')
class OnboardingRequestEditView(ObjectEditView):
    queryset = OnboardingRequest.objects.all()
    form = forms.OnboardingRequestForm

    def alter_object(self, obj, request, url_args, url_kwargs):
        if obj.pk is None:
            obj.requested_by = request.user
        return obj


@register_model_view(OnboardingRequest, 'delete')
class OnboardingRequestDeleteView(ObjectDeleteView):
    queryset = OnboardingRequest.objects.all()


class OnboardingRequestBulkImportView(BulkImportView):
    queryset = OnboardingRequest.objects.all()
    model_form = forms.OnboardingRequestImportForm


class OnboardingRequestBulkDeleteView(BulkDeleteView):
    queryset = OnboardingRequest.objects.select_related('site', 'poller')
    filterset = filtersets.OnboardingRequestFilterSet
    table = tables.OnboardingRequestTable


class _ReviewActionView(View):
    """Shared plumbing for the approve/reject/retry buttons."""

    permission_required = 'netbox_discovery.change_onboardingrequest'

    def get_object(self, pk):
        return get_object_or_404(OnboardingRequest.objects.all(), pk=pk)

    def deny(self, request, entry, why):
        messages.error(request, why)
        return redirect(entry.get_absolute_url())


class OnboardingApproveView(_ReviewActionView):
    """Approve a scanned request so its poller applies it on the next check-in.

    Approving does not create anything here. The poller owns every write into
    DCIM, because all the idempotent create logic — device types, stacks into
    virtual chassis, module bays, interfaces — already lives in the scanner and
    a second copy in NetBox would be a second thing to keep correct.
    """

    def post(self, request, pk):
        entry = self.get_object(pk)
        if not request.user.has_perm(self.permission_required):
            return self.deny(request, entry, 'You do not have permission to approve.')
        form = forms.OnboardingReviewForm(request.POST)
        if not form.is_valid():
            return self.deny(request, entry, 'Check the overrides and try again.')

        try:
            actions.approve(
                entry, user=request.user,
                override_name=form.cleaned_data['override_name'],
                override_site=form.cleaned_data['override_site'],
                role=form.cleaned_data['role'],
            )
        except actions.TransitionError as exc:
            return self.deny(request, entry, str(exc))

        poller = entry.poller
        if poller is not None and poller.is_stale:
            messages.warning(
                request,
                'Approved, but poller %s has not checked in recently — it will be '
                'applied whenever that poller next runs.' % poller.name,
            )
        else:
            messages.success(
                request,
                'Approved. Poller %s will apply it on its next check-in.'
                % (poller.name if poller else '—'),
            )
        return redirect(entry.get_absolute_url())


class OnboardingRejectView(_ReviewActionView):
    def post(self, request, pk):
        entry = self.get_object(pk)
        if not request.user.has_perm(self.permission_required):
            return self.deny(request, entry, 'You do not have permission to reject.')
        try:
            actions.reject(entry, user=request.user)
        except actions.TransitionError as exc:
            return self.deny(request, entry, str(exc))
        messages.success(request, 'Rejected. Nothing was written to DCIM.')
        return redirect(entry.get_absolute_url())


class OnboardingRetryView(_ReviewActionView):
    """Put a failed or unresolvable request back in the queue.

    Re-runs resolution on the way, so the usual fix — create the missing
    prefix, or tag the site — is picked up without anybody re-typing the
    address.
    """

    def post(self, request, pk):
        entry = self.get_object(pk)
        if not request.user.has_perm(self.permission_required):
            return self.deny(request, entry, 'You do not have permission to retry.')

        try:
            actions.retry(entry)
        except actions.TransitionError as exc:
            return self.deny(request, entry, 'Still cannot queue that address: %s' % exc)
        messages.success(
            request, 'Queued again for poller %s.'
            % (entry.poller.name if entry.poller else '—')
        )
        return redirect(entry.get_absolute_url())


class OnboardingManualEntryView(_ReviewActionView):
    """Take hardware details by hand for a device SNMP cannot reach."""

    def post(self, request, pk):
        entry = self.get_object(pk)
        if not request.user.has_perm(self.permission_required):
            return self.deny(request, entry, 'You do not have permission to do that.')

        form = forms.OnboardingManualEntryForm(request.POST)
        if not form.is_valid():
            return self.deny(
                request, entry,
                'Check the details: %s' % form.errors.as_text().replace('\n', ' '),
            )
        try:
            actions.enter_manually(entry, user=request.user, **form.cleaned_data)
        except actions.TransitionError as exc:
            return self.deny(request, entry, str(exc))

        messages.success(
            request,
            'Recorded by hand. Poller %s will create it on its next check-in.'
            % (entry.poller.name if entry.poller else '—'),
        )
        return redirect(entry.get_absolute_url())


@register_model_view(DiscoveryPoller, name='list')
class DiscoveryPollerListView(ObjectListView):
    queryset = DiscoveryPoller.objects.annotate(
        requests__count=Count(
            'requests',
            filter=~Q(requests__status__in=OnboardingStatusChoices.TERMINAL),
        )
    )
    table = tables.DiscoveryPollerTable
    filterset = filtersets.DiscoveryPollerFilterSet
    filterset_form = forms.DiscoveryPollerFilterForm


@register_model_view(DiscoveryPoller)
class DiscoveryPollerView(ObjectView):
    queryset = DiscoveryPoller.objects.all()

    def get_extra_context(self, request, instance):
        return {
            # Coverage is computed rather than stored: it comes from the same
            # tags the scanner reads, so showing a stored copy could disagree
            # with what the poller actually does.
            'sites': sites_for_poller(instance.name),
            'open_requests': instance.requests.exclude(
                status__in=OnboardingStatusChoices.TERMINAL
            ).select_related('site')[:50],
        }


@register_model_view(DiscoveryPoller, 'edit')
class DiscoveryPollerEditView(ObjectEditView):
    queryset = DiscoveryPoller.objects.all()
    form = forms.DiscoveryPollerForm


@register_model_view(DiscoveryPoller, 'delete')
class DiscoveryPollerDeleteView(ObjectDeleteView):
    queryset = DiscoveryPoller.objects.all()


class DiscoveryPollerBulkDeleteView(BulkDeleteView):
    queryset = DiscoveryPoller.objects.all()
    filterset = filtersets.DiscoveryPollerFilterSet
    table = tables.DiscoveryPollerTable


@register_model_view(HardwareReplacement, name='list')
class HardwareReplacementListView(ObjectListView):
    queryset = HardwareReplacement.objects.select_related(
        'device', 'replaced_device', 'poller'
    )
    table = tables.HardwareReplacementTable
    filterset = filtersets.HardwareReplacementFilterSet


@register_model_view(HardwareReplacement)
class HardwareReplacementView(ObjectView):
    queryset = HardwareReplacement.objects.select_related(
        'device', 'replaced_device', 'poller'
    )


@register_model_view(HardwareReplacement, 'delete')
class HardwareReplacementDeleteView(ObjectDeleteView):
    queryset = HardwareReplacement.objects.all()


class HardwareReplacementBulkDeleteView(BulkDeleteView):
    queryset = HardwareReplacement.objects.select_related('device')
    filterset = filtersets.HardwareReplacementFilterSet
    table = tables.HardwareReplacementTable


@register_model_view(DiscoveryIssue, name='list')
class DiscoveryIssueListView(ObjectListView):
    queryset = DiscoveryIssue.objects.select_related('device', 'poller')
    table = tables.DiscoveryIssueTable
    filterset = filtersets.DiscoveryIssueFilterSet


@register_model_view(DiscoveryIssue)
class DiscoveryIssueView(ObjectView):
    queryset = DiscoveryIssue.objects.select_related('device', 'poller')


@register_model_view(DiscoveryIssue, 'edit')
class DiscoveryIssueEditView(ObjectEditView):
    queryset = DiscoveryIssue.objects.all()
    form = forms.DiscoveryIssueForm


@register_model_view(DiscoveryIssue, 'delete')
class DiscoveryIssueDeleteView(ObjectDeleteView):
    queryset = DiscoveryIssue.objects.all()


class DiscoveryIssueBulkDeleteView(BulkDeleteView):
    queryset = DiscoveryIssue.objects.select_related('device')
    filterset = filtersets.DiscoveryIssueFilterSet
    table = tables.DiscoveryIssueTable
