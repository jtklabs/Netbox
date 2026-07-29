from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import get_object_or_404, redirect
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
