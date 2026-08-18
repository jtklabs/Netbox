"""Views: CRUD for both models, plus the fleet report.

The report is one page rather than two because both halves are built from the
same rows. The per-standard rollup ("how many devices are missing X") and the
per-device detail underneath it therefore cannot disagree — computing the
summary with its own query is the classic way a compliance page ends up
claiming a percentage its own table contradicts.
"""

from dcim.models import Device, Region, Site
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count
from django.shortcuts import render
from django.views import View
from netbox.views.generic import (
    BulkDeleteView,
    BulkEditView,
    ObjectDeleteView,
    ObjectEditView,
    ObjectListView,
    ObjectView,
)
from utilities.views import register_model_view

from netbox_compliance import filtersets, forms, scoping, tables
from netbox_compliance.models import ConfigCompliance, ConfigStandard

__all__ = (
    'ConfigStandardListView',
    'ConfigStandardView',
    'ConfigStandardEditView',
    'ConfigStandardDeleteView',
    'ConfigStandardBulkEditView',
    'ConfigStandardBulkDeleteView',
    'ConfigComplianceListView',
    'ConfigComplianceView',
    'ConfigComplianceEditView',
    'ConfigComplianceDeleteView',
    'ConfigComplianceBulkEditView',
    'ConfigComplianceBulkDeleteView',
    'ComplianceReportView',
)


@register_model_view(ConfigStandard, name='list')
class ConfigStandardListView(ObjectListView):
    # result_count is annotated rather than counted per row: the list renders
    # every standard, and a property doing .results.count() would be one query
    # per row for a column somebody glances at.
    queryset = ConfigStandard.objects.prefetch_related(
        'platforms', 'roles', 'sites', 'device_tags', 'tags'
    ).annotate(result_count=Count('results', distinct=True))
    table = tables.ConfigStandardTable
    filterset = filtersets.ConfigStandardFilterSet
    filterset_form = forms.ConfigStandardFilterForm


@register_model_view(ConfigStandard)
class ConfigStandardView(ObjectView):
    queryset = ConfigStandard.objects.prefetch_related(
        'platforms', 'roles', 'sites', 'device_tags'
    )

    def get_extra_context(self, request, instance):
        results = instance.results.select_related('device', 'device__site').order_by(
            'device__name'
        )
        rows = [
            {
                'device': record.device,
                'standard': instance,
                'record': record,
                'status': record.status,
                'status_label': record.get_status_display(),
                'status_color': record.get_status_color(),
                'findings': record.finding_count,
                'last_checked': record.last_checked,
                'is_stale': record.is_stale,
                'needs_manual_fix': record.needs_manual_fix,
            }
            for record in results[:100]
        ]
        return {
            'result_table': tables.ComplianceReportTable(rows),
            'result_total': results.count(),
            'summary': scoping.summarise(rows),
            'runtime_variables': instance.runtime_variables,
        }


@register_model_view(ConfigStandard, 'edit')
class ConfigStandardEditView(ObjectEditView):
    queryset = ConfigStandard.objects.all()
    form = forms.ConfigStandardForm


@register_model_view(ConfigStandard, 'delete')
class ConfigStandardDeleteView(ObjectDeleteView):
    queryset = ConfigStandard.objects.all()


@register_model_view(ConfigStandard, 'bulk_edit')
class ConfigStandardBulkEditView(BulkEditView):
    queryset = ConfigStandard.objects.all()
    filterset = filtersets.ConfigStandardFilterSet
    table = tables.ConfigStandardTable
    form = forms.ConfigStandardBulkEditForm


@register_model_view(ConfigStandard, 'bulk_delete')
class ConfigStandardBulkDeleteView(BulkDeleteView):
    queryset = ConfigStandard.objects.all()
    filterset = filtersets.ConfigStandardFilterSet
    table = tables.ConfigStandardTable


@register_model_view(ConfigCompliance, name='list')
class ConfigComplianceListView(ObjectListView):
    queryset = ConfigCompliance.objects.select_related(
        'device', 'device__site', 'standard'
    ).prefetch_related('tags')
    table = tables.ConfigComplianceTable
    filterset = filtersets.ConfigComplianceFilterSet
    filterset_form = forms.ConfigComplianceFilterForm


@register_model_view(ConfigCompliance)
class ConfigComplianceView(ObjectView):
    queryset = ConfigCompliance.objects.select_related('device', 'standard')


@register_model_view(ConfigCompliance, 'edit')
class ConfigComplianceEditView(ObjectEditView):
    queryset = ConfigCompliance.objects.all()
    form = forms.ConfigComplianceForm


@register_model_view(ConfigCompliance, 'delete')
class ConfigComplianceDeleteView(ObjectDeleteView):
    queryset = ConfigCompliance.objects.all()


@register_model_view(ConfigCompliance, 'bulk_edit')
class ConfigComplianceBulkEditView(BulkEditView):
    queryset = ConfigCompliance.objects.select_related('device', 'standard')
    filterset = filtersets.ConfigComplianceFilterSet
    table = tables.ConfigComplianceTable
    form = forms.ConfigComplianceBulkEditForm


@register_model_view(ConfigCompliance, 'bulk_delete')
class ConfigComplianceBulkDeleteView(BulkDeleteView):
    queryset = ConfigCompliance.objects.select_related('device', 'standard')
    filterset = filtersets.ConfigComplianceFilterSet
    table = tables.ConfigComplianceTable


def _sites_in_scope(regions, sites):
    """The sites a report is scoped to, from either filter or both.

    Lifted from netbox_refresh's report for the same reasons: regions nest, so
    selecting EMEA has to mean every site beneath it, and giving both filters
    reads as narrowing rather than adding. None means "no scope", which is not
    the same as an empty list — that would mean no sites at all.
    """
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


class ComplianceReportView(PermissionRequiredMixin, View):
    """Fleet configuration compliance: the rollup, then the rows behind it.

    Every device in scope for a standard appears, including devices nobody has
    ever checked. That is the whole point — a report assembled from recorded
    results only would get greener the less checking anyone did.
    """

    permission_required = 'netbox_compliance.view_configcompliance'
    template_name = 'netbox_compliance/compliance_report.html'

    def get(self, request):
        form = forms.ComplianceReportForm(request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound else {}

        devices = Device.objects.select_related('site', 'platform', 'role')
        sites = _sites_in_scope(data.get('region'), data.get('site'))
        if sites is not None:
            devices = devices.filter(site__in=sites)
        if data.get('platform'):
            devices = devices.filter(platform__in=data['platform'])
        if data.get('role'):
            devices = devices.filter(role__in=data['role'])

        chosen_standards = data.get('standard')
        standards = None
        if chosen_standards:
            standards = list(
                scoping.active_standards(
                    queryset=ConfigStandard.objects.filter(
                        pk__in=[s.pk for s in chosen_standards]
                    )
                ).prefetch_related('platforms', 'roles', 'sites', 'device_tags')
            )

        resolver = scoping.StandardResolver(standards=standards)
        # Device tags are only worth a prefetch when some standard scopes by
        # them; otherwise it is a join nobody reads.
        if resolver.uses_device_tags:
            devices = devices.prefetch_related('tags')

        rows = scoping.device_standard_rows(devices, standards=resolver.standards)
        summary = scoping.summarise(rows)
        rollup = scoping.standard_rollup(rows)

        wanted = data.get('status')
        shown = [row for row in rows if row['status'] in wanted] if wanted else rows

        return render(request, self.template_name, {
            'form': form,
            'summary': summary,
            'rollup_table': tables.StandardRollupTable(rollup),
            'table': tables.ComplianceReportTable(shown),
            'row_count': len(shown),
            'total_count': len(rows),
            'device_count': devices.count(),
            'standard_count': len(resolver.standards),
        })
