from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
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

from netbox_quotes import filtersets, forms, tables
from netbox_quotes.matching import rematch_quote
from netbox_quotes.models import Quote, QuoteLine, Vendor


# ---------------------------------------------------------------- Vendors
@register_model_view(Vendor, name='list')
class VendorListView(ObjectListView):
    queryset = Vendor.objects.all()
    table = tables.VendorTable
    filterset = filtersets.VendorFilterSet
    filterset_form = forms.VendorFilterForm


@register_model_view(Vendor)
class VendorView(ObjectView):
    queryset = Vendor.objects.all()

    def get_extra_context(self, request, instance):
        quotes_table = tables.QuoteTable(instance.quotes.all())
        quotes_table.configure(request)
        return {'quotes_table': quotes_table}


@register_model_view(Vendor, 'edit')
class VendorEditView(ObjectEditView):
    queryset = Vendor.objects.all()
    form = forms.VendorForm


@register_model_view(Vendor, 'delete')
class VendorDeleteView(ObjectDeleteView):
    queryset = Vendor.objects.all()


@register_model_view(Vendor, 'bulk_delete')
class VendorBulkDeleteView(BulkDeleteView):
    queryset = Vendor.objects.all()
    filterset = filtersets.VendorFilterSet
    table = tables.VendorTable


@register_model_view(Vendor, 'bulk_import')
class VendorBulkImportView(BulkImportView):
    queryset = Vendor.objects.all()
    model_form = forms.VendorImportForm


# ---------------------------------------------------------------- Quotes
@register_model_view(Quote, name='list')
class QuoteListView(ObjectListView):
    queryset = Quote.objects.all()
    table = tables.QuoteTable
    filterset = filtersets.QuoteFilterSet
    filterset_form = forms.QuoteFilterForm


@register_model_view(Quote)
class QuoteView(ObjectView):
    queryset = Quote.objects.all()

    def get_extra_context(self, request, instance):
        lines_table = tables.QuoteLineTable(
            instance.lines.prefetch_related('assigned_object')
        )
        lines_table.configure(request)
        return {'lines_table': lines_table}


@register_model_view(Quote, 'edit')
class QuoteEditView(ObjectEditView):
    queryset = Quote.objects.all()
    form = forms.QuoteForm


@register_model_view(Quote, 'delete')
class QuoteDeleteView(ObjectDeleteView):
    queryset = Quote.objects.all()


@register_model_view(Quote, 'bulk_delete')
class QuoteBulkDeleteView(BulkDeleteView):
    queryset = Quote.objects.all()
    filterset = filtersets.QuoteFilterSet
    table = tables.QuoteTable


@register_model_view(Quote, 'bulk_import')
class QuoteBulkImportView(BulkImportView):
    queryset = Quote.objects.all()
    model_form = forms.QuoteImportForm


class QuoteRematchView(PermissionRequiredMixin, View):
    """POST action: re-run serial matching for all non-manual lines of a quote."""

    permission_required = 'netbox_quotes.change_quoteline'

    def post(self, request, pk):
        quote = get_object_or_404(Quote, pk=pk)
        results = rematch_quote(quote)
        summary = (
            ', '.join(f'{count} {state}' for state, count in sorted(results.items()))
            or 'no eligible lines'
        )
        messages.success(request, f'Re-matched quote lines: {summary}.')
        return redirect(quote.get_absolute_url())


# ---------------------------------------------------------------- Quote lines
@register_model_view(QuoteLine, name='list')
class QuoteLineListView(ObjectListView):
    queryset = QuoteLine.objects.prefetch_related('quote', 'assigned_object')
    table = tables.QuoteLineTable
    filterset = filtersets.QuoteLineFilterSet
    filterset_form = forms.QuoteLineFilterForm


@register_model_view(QuoteLine)
class QuoteLineView(ObjectView):
    queryset = QuoteLine.objects.prefetch_related('quote', 'assigned_object')


@register_model_view(QuoteLine, 'edit')
class QuoteLineEditView(ObjectEditView):
    queryset = QuoteLine.objects.all()
    form = forms.QuoteLineForm


@register_model_view(QuoteLine, 'delete')
class QuoteLineDeleteView(ObjectDeleteView):
    queryset = QuoteLine.objects.all()


@register_model_view(QuoteLine, 'bulk_delete')
class QuoteLineBulkDeleteView(BulkDeleteView):
    queryset = QuoteLine.objects.all()
    filterset = filtersets.QuoteLineFilterSet
    table = tables.QuoteLineTable


@register_model_view(QuoteLine, 'bulk_import')
class QuoteLineBulkImportView(BulkImportView):
    queryset = QuoteLine.objects.all()
    model_form = forms.QuoteLineImportForm


# ---------------------------------------------------------------- Reports

def _sites_in_scope(regions, sites):
    """Sites selected directly, or belonging to the selected regions (nested
    regions included). Empty selections mean no site restriction."""
    from dcim.models import Region, Site

    if not regions and not sites:
        return None
    if not regions:
        return list(sites)
    within = Site.objects.filter(
        region__in=Region.objects.filter(
            pk__in=[r.pk for r in regions]
        ).get_descendants(include_self=True)
    )
    if sites:
        chosen = {s.pk for s in sites}
        return [s for s in within if s.pk in chosen]
    return list(within)


def _bucket_counts(coverages, today):
    """The always-shown 30/60/90 summary, independent of the custom horizon."""
    buckets = {'expired': 0, 'in_30': 0, 'in_60': 0, 'in_90': 0}
    for coverage in coverages:
        days = (coverage.coverage_end - today).days
        if days < 0:
            buckets['expired'] += 1
        elif days <= 30:
            buckets['in_30'] += 1
        elif days <= 60:
            buckets['in_60'] += 1
        elif days <= 90:
            buckets['in_90'] += 1
    return buckets


class CoverageExpiryReportView(PermissionRequiredMixin, View):
    """Support coverage running out: the renewal work queue.

    Device-level on purpose. Coverage lives on quote lines, but the question
    is "which boxes are about to be unsupported" — a device's later renewal
    line supersedes an earlier expiry, so only the latest coverage_end per
    device counts.
    """

    permission_required = 'netbox_quotes.view_quoteline'
    template_name = 'netbox_quotes/coverage_expiry_report.html'

    def get(self, request):
        from datetime import date, timedelta

        from netbox_quotes import reports

        form = forms.CoverageExpiryReportForm(request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound else {}

        today = date.today()
        horizon = data.get('until') or (today + timedelta(days=90))
        sites = _sites_in_scope(data.get('region'), data.get('site'))
        vendors = data.get('vendor')
        # Unbound form (first page load): the field's initial does not reach
        # cleaned_data, so default the flag on explicitly.
        include_expired = data.get('include_expired', True) if form.is_bound else True

        coverages = list(reports.device_coverage_map().values())
        if sites is not None:
            site_pks = {s.pk for s in sites}
            coverages = [c for c in coverages if c.device.site_id in site_pks]
        if vendors:
            vendor_pks = {v.pk for v in vendors}
            coverages = [c for c in coverages if c.vendor.pk in vendor_pks]

        buckets = _bucket_counts(coverages, today)

        rows = []
        for coverage in coverages:
            if coverage.coverage_end > horizon:
                continue
            if not include_expired and coverage.coverage_end < today:
                continue
            device = coverage.device
            rows.append({
                'device': device,
                'device_url': device.get_absolute_url(),
                'site': device.site,
                'device_type': device.device_type,
                'serial': device.serial or '—',
                'vendor': coverage.vendor,
                'vendor_url': coverage.vendor.get_absolute_url(),
                'quote': coverage.quote.number,
                'quote_url': coverage.quote.get_absolute_url(),
                'coverage_end': coverage.coverage_end,
                'days_left': coverage.days_left,
                'days_overdue': -coverage.days_left,
            })
        rows.sort(key=lambda r: r['coverage_end'])

        return render(request, self.template_name, {
            'form': form,
            'table': tables.CoverageExpiryTable(rows),
            'row_count': len(rows),
            'buckets': buckets,
            'horizon': horizon,
            'covered_total': len(coverages),
        })


class EolTransitionReportView(PermissionRequiredMixin, View):
    """Devices whose hardware is reaching manufacturer end of life, and where
    their support coverage stands.

    Once a model passes the manufacturer's end of support, OEM coverage stops
    being renewable and support has to move to a third-party maintenance
    vendor. This report is that work queue: every device of an EoL (or
    soon-EoL) model, classified by whether its coverage already sits with a
    third-party vendor, still sits with the OEM, or has lapsed entirely.

    Lifecycle dates come from the Hardware Lifecycle plugin. The dependency is
    soft — without that plugin the report explains itself rather than erroring.
    """

    permission_required = 'netbox_quotes.view_quoteline'
    template_name = 'netbox_quotes/eol_transition_report.html'

    def get(self, request):
        from datetime import date, timedelta

        from netbox_quotes import reports

        try:
            from netbox_refresh.models import (
                EFFECTIVE_EOL_ALIAS,
                ModelLifecycle,
                effective_end_of_life_expression,
            )
        except ImportError:
            return render(request, self.template_name, {
                'lifecycle_missing': True, 'form': None,
            })

        from dcim.models import Device
        from django.contrib.contenttypes.models import ContentType

        form = forms.EolTransitionReportForm(request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound else {}

        today = date.today()
        horizon = data.get('until') or (today + timedelta(days=90))
        sites = _sites_in_scope(data.get('region'), data.get('site'))
        include_transitioned = data.get('include_transitioned', False)

        # Device-type lifecycles whose effective EoL is inside the horizon.
        # Uses the same effective end-of-life the Lifecycle plugin reports —
        # the soonest of end-of-support and end-of-security-support — so the
        # two plugins never disagree about when a model is "done".
        device_type_ct = ContentType.objects.get_by_natural_key('dcim', 'devicetype')
        lifecycles = ModelLifecycle.objects.filter(
            assigned_object_type=device_type_ct
        ).annotate(
            **{EFFECTIVE_EOL_ALIAS: effective_end_of_life_expression()}
        ).filter(**{
            '%s__isnull' % EFFECTIVE_EOL_ALIAS: False,
            '%s__lte' % EFFECTIVE_EOL_ALIAS: horizon,
        })
        eol_by_type = {
            lc.assigned_object_id: getattr(lc, EFFECTIVE_EOL_ALIAS) for lc in lifecycles
        }
        if not eol_by_type:
            return render(request, self.template_name, {
                'form': form,
                'table': tables.EolTransitionTable([]),
                'row_count': 0,
                'summary': {'uncovered': 0, 'transition': 0, 'transitioned': 0},
                'horizon': horizon,
            })

        devices = Device.objects.filter(
            device_type_id__in=eol_by_type
        ).select_related('site', 'device_type', 'device_type__manufacturer')
        if sites is not None:
            devices = devices.filter(site__in=[s.pk for s in sites])
        devices = list(devices)

        coverage_map = reports.device_coverage_map([d.pk for d in devices])

        rows = []
        summary = {'uncovered': 0, 'transition': 0, 'transitioned': 0}
        for device in devices:
            coverage = coverage_map.get(device.pk)
            if coverage is None or coverage.coverage_end < today:
                state = 'uncovered'
            elif coverage.is_third_party:
                state = 'transitioned'
            else:
                state = 'transition'
            summary[state] += 1
            if state == 'transitioned' and not include_transitioned:
                continue
            rows.append({
                'device': device,
                'device_url': device.get_absolute_url(),
                'site': device.site,
                'device_type': device.device_type,
                'serial': device.serial or '—',
                'eol_date': eol_by_type[device.device_type_id],
                'vendor': coverage.vendor if coverage else '—',
                'vendor_url': coverage.vendor.get_absolute_url() if coverage else None,
                'coverage_end': coverage.coverage_end if coverage else None,
                'state': state,
            })
        # Most urgent first: uncovered, then OEM-covered, by EoL date within.
        order = {'uncovered': 0, 'transition': 1, 'transitioned': 2}
        rows.sort(key=lambda r: (order[r['state']], r['eol_date']))

        return render(request, self.template_name, {
            'form': form,
            'table': tables.EolTransitionTable(rows),
            'row_count': len(rows),
            'summary': summary,
            'horizon': horizon,
        })
