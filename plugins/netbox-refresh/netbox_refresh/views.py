from collections import defaultdict

from dcim.models import Device, DeviceType, Module, ModuleType, Region, Site
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.views import View
from netbox.views.generic import (
    BulkDeleteView,
    BulkEditView,
    BulkImportView,
    ObjectDeleteView,
    ObjectEditView,
    ObjectListView,
    ObjectView,
)
from utilities.views import register_model_view

from netbox_refresh import compliance, filtersets, forms, tables
from netbox_refresh.pricing import PriceResolver
from netbox_refresh.models import (
    EFFECTIVE_EOL_ALIAS,
    effective_end_of_life_expression,
)
from netbox_refresh.models import (
    DeviceSoftware,
    ModelLifecycle,
    ReplacementPrice,
    SoftwareStandard,
    SoftwareVersion,
)


@register_model_view(ModelLifecycle, name='list')
class ModelLifecycleListView(ObjectListView):
    queryset = ModelLifecycle.objects.prefetch_related(
        'assigned_object_type', 'replacement_device_type', 'replacement_module_type'
    )
    table = tables.ModelLifecycleTable
    filterset = filtersets.ModelLifecycleFilterSet
    filterset_form = forms.ModelLifecycleFilterForm


@register_model_view(ModelLifecycle)
class ModelLifecycleView(ObjectView):
    queryset = ModelLifecycle.objects.all()

    def get_extra_context(self, request, instance):
        obj = instance.assigned_object
        devices = Module.objects.none()
        if obj is not None and obj._meta.model_name == 'devicetype':
            devices = Device.objects.filter(device_type=obj).select_related('site')[:50]
        elif obj is not None:
            devices = Module.objects.filter(module_type=obj).select_related('device')[:50]
        return {'installed': devices}


@register_model_view(ModelLifecycle, 'edit')
class ModelLifecycleEditView(ObjectEditView):
    queryset = ModelLifecycle.objects.all()
    form = forms.ModelLifecycleForm


@register_model_view(ModelLifecycle, 'delete')
class ModelLifecycleDeleteView(ObjectDeleteView):
    queryset = ModelLifecycle.objects.all()


@register_model_view(ModelLifecycle, 'bulk_edit')
class ModelLifecycleBulkEditView(BulkEditView):
    queryset = ModelLifecycle.objects.all()
    filterset = filtersets.ModelLifecycleFilterSet
    table = tables.ModelLifecycleTable
    form = forms.ModelLifecycleBulkEditForm


@register_model_view(ModelLifecycle, 'bulk_delete')
class ModelLifecycleBulkDeleteView(BulkDeleteView):
    queryset = ModelLifecycle.objects.all()
    filterset = filtersets.ModelLifecycleFilterSet
    table = tables.ModelLifecycleTable


@register_model_view(ModelLifecycle, 'bulk_import')
class ModelLifecycleBulkImportView(BulkImportView):
    queryset = ModelLifecycle.objects.all()
    model_form = forms.ModelLifecycleImportForm


# --------------------------------------------------------------------------- #
# Replacement prices
# --------------------------------------------------------------------------- #

@register_model_view(ReplacementPrice, name='list')
class ReplacementPriceListView(ObjectListView):
    queryset = ReplacementPrice.objects.select_related(
        'lifecycle', 'lifecycle__assigned_object_type', 'region', 'site'
    )
    table = tables.ReplacementPriceTable
    filterset = filtersets.ReplacementPriceFilterSet
    filterset_form = forms.ReplacementPriceFilterForm


@register_model_view(ReplacementPrice)
class ReplacementPriceView(ObjectView):
    queryset = ReplacementPrice.objects.select_related('lifecycle', 'region', 'site')


@register_model_view(ReplacementPrice, 'edit')
class ReplacementPriceEditView(ObjectEditView):
    queryset = ReplacementPrice.objects.all()
    form = forms.ReplacementPriceForm


@register_model_view(ReplacementPrice, 'delete')
class ReplacementPriceDeleteView(ObjectDeleteView):
    queryset = ReplacementPrice.objects.all()


@register_model_view(ReplacementPrice, 'bulk_delete')
class ReplacementPriceBulkDeleteView(BulkDeleteView):
    queryset = ReplacementPrice.objects.all()
    filterset = filtersets.ReplacementPriceFilterSet
    table = tables.ReplacementPriceTable


@register_model_view(ReplacementPrice, 'bulk_import')
class ReplacementPriceBulkImportView(BulkImportView):
    queryset = ReplacementPrice.objects.all()
    model_form = forms.ReplacementPriceImportForm


def _sites_in_scope(regions, sites):
    """The sites a report is scoped to, from either filter or both.

    Regions nest, so selecting EMEA has to mean every site under it and not
    just the ones parented directly to it — otherwise the report quietly
    reports on a fraction of the estate and reads as though the rest owns no
    hardware.

    Given both, the intersection is used: two filters shown together read as
    narrowing, and a site outside the chosen regions is a contradiction rather
    than an addition. Returning None means "no scope", which is not the same
    as an empty list — that would be "no sites at all" and would count nothing.
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


def _money(per_currency):
    """One cell for a currency->amount mapping: '12,000.00 USD + 9,500.00 EUR'.

    Never converted or combined — any exchange rate hardcoded here would be
    wrong by the time it was read, so each currency keeps its own number.
    """
    if not per_currency:
        return '—'
    return ' + '.join(
        '{:,.2f} {}'.format(amount, currency)
        for currency, amount in sorted(per_currency.items())
    )


def _region_rows(region_agg):
    """The per-region cost breakdown, labelled with each region's tree path."""
    all_regions = Region.objects.in_bulk()

    def label(region_id):
        parts = []
        seen = set()
        while region_id is not None and region_id not in seen:
            seen.add(region_id)
            region = all_regions.get(region_id)
            if region is None:
                break
            parts.append(region.name)
            region_id = region.parent_id
        return ' / '.join(reversed(parts))

    rows = []
    for region_id, agg in region_agg.items():
        region = all_regions.get(region_id)
        rows.append({
            'region': label(region_id) if region else '(no region)',
            'region_url': region.get_absolute_url() if region else None,
            'units': agg['units'],
            'total': _money(agg['totals']),
            'unpriced': agg['unpriced'] or '',
        })
    # Alphabetical by path keeps siblings together; the regionless bucket last.
    rows.sort(key=lambda r: (r['region_url'] is None, r['region']))
    return rows


class RefreshReportView(PermissionRequiredMixin, View):
    """Hardware reaching a lifecycle milestone in a window, with replacement cost.

    Installed counts are gathered per model and site with two aggregate
    queries, so the report stays cheap as the estate grows — and priced per
    site, because ReplacementPrice lets the same model cost different money
    in different places.
    """

    permission_required = 'netbox_refresh.view_modellifecycle'
    template_name = 'netbox_refresh/refresh_report.html'

    def get(self, request):
        form = forms.RefreshReportForm(request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound else {}

        date_field = data.get('date_field') or EFFECTIVE_EOL_ALIAS
        after = data.get('after')
        before = data.get('before')
        manufacturers = data.get('manufacturer')
        sites = _sites_in_scope(data.get('region'), data.get('site'))

        # Annotated unconditionally so the report can filter and sort on the
        # effective end-of-life the same way it does a stored column; the
        # model property cannot be used in a query.
        queryset = ModelLifecycle.objects.prefetch_related(
            'assigned_object_type', 'replacement_device_type', 'replacement_module_type'
        ).annotate(
            **{EFFECTIVE_EOL_ALIAS: effective_end_of_life_expression()}
        ).filter(**{'%s__isnull' % date_field: False})
        if after:
            queryset = queryset.filter(**{'%s__gte' % date_field: after})
        if before:
            queryset = queryset.filter(**{'%s__lte' % date_field: before})

        records = list(queryset)

        device_type_ids = [r.assigned_object_id for r in records
                           if r.assigned_object_type.model == 'devicetype']
        module_type_ids = [r.assigned_object_id for r in records
                           if r.assigned_object_type.model == 'moduletype']

        # Counts are per (model, site), not just per model: the same box does
        # not cost the same in every country, so money can only be summed
        # after each site's units are priced at that site's rate.
        device_site_counts = defaultdict(dict)   # type_id -> {site_id: n}
        if device_type_ids:
            device_qs = Device.objects.filter(device_type_id__in=device_type_ids)
            if sites:
                device_qs = device_qs.filter(site__in=sites)
            for row in device_qs.values('device_type_id', 'site_id').annotate(n=Count('pk')):
                device_site_counts[row['device_type_id']][row['site_id']] = row['n']

        module_site_counts = defaultdict(dict)   # type_id -> {site_id: n}
        if module_type_ids:
            module_qs = Module.objects.filter(module_type_id__in=module_type_ids)
            if sites:
                module_qs = module_qs.filter(device__site__in=sites)
            for row in module_qs.values('module_type_id', 'device__site_id').annotate(n=Count('pk')):
                module_site_counts[row['module_type_id']][row['device__site_id']] = row['n']

        type_cache = {
            'devicetype': DeviceType.objects.in_bulk(device_type_ids),
            'moduletype': ModuleType.objects.in_bulk(module_type_ids),
        }
        involved_site_ids = {
            site_id
            for per_site in list(device_site_counts.values()) + list(module_site_counts.values())
            for site_id in per_site
        }
        site_cache = Site.objects.in_bulk(involved_site_ids)
        resolver = PriceResolver([r.pk for r in records])

        rows = []
        totals = defaultdict(lambda: 0)
        total_units = 0
        models_with_gaps = 0
        unpriced_units = 0
        # region_id (None = siteless bucket) -> aggregation for the breakdown
        region_agg = defaultdict(lambda: {'units': 0, 'unpriced': 0,
                                          'totals': defaultdict(lambda: 0)})
        for record in records:
            model_name = record.assigned_object_type.model
            obj = type_cache.get(model_name, {}).get(record.assigned_object_id)
            if obj is None:
                continue
            if manufacturers and obj.manufacturer_id not in {m.pk for m in manufacturers}:
                continue
            per_site = (device_site_counts if model_name == 'devicetype'
                        else module_site_counts)[obj.pk]
            installed = sum(per_site.values())
            if sites and not installed:
                continue  # nothing of this model at the selected sites

            extended = defaultdict(lambda: 0)    # currency -> amount, this model
            model_unpriced = 0
            for site_id, count in per_site.items():
                site = site_cache.get(site_id)
                hit = resolver.resolve(record, site)
                bucket = region_agg[site.region_id if site else None]
                bucket['units'] += count
                if hit is None:
                    model_unpriced += count
                    bucket['unpriced'] += count
                    continue
                amount = hit.cost * count
                extended[hit.currency] += amount
                totals[hit.currency] += amount
                bucket['totals'][hit.currency] += amount
            if model_unpriced:
                models_with_gaps += 1
                unpriced_units += model_unpriced
            total_units += installed

            regional = resolver.price_count(record.pk)
            if record.replacement_cost is not None:
                unit_cost = '%s %s' % (record.replacement_cost, record.currency)
                if regional:
                    unit_cost += ' (+%d regional)' % regional
            else:
                unit_cost = '%d regional price%s' % (regional, 's' if regional != 1 else '') \
                    if regional else '—'

            replacement = record.replacement
            rows.append({
                'model': str(obj),
                'url': record.get_absolute_url(),
                'manufacturer': obj.manufacturer,
                'part_number': obj.part_number or '—',
                'milestone_date': getattr(record, date_field),
                'installed': installed,
                'replacement': replacement or '—',
                'replacement_url': replacement.get_absolute_url() if replacement else None,
                'unit_cost': unit_cost,
                'extended_cost': _money(extended),
            })

        rows.sort(key=lambda r: (r['milestone_date'] is None, r['milestone_date']))
        table = tables.RefreshReportTable(rows)

        return render(request, self.template_name, {
            'form': form,
            'table': table,
            'region_table': tables.RegionCostTable(_region_rows(region_agg)),
            'row_count': len(rows),
            'total_units': total_units,
            'totals': dict(totals),
            'missing_cost': models_with_gaps,
            'unpriced_units': unpriced_units,
            'date_label': dict(forms.RefreshReportForm.DATE_FIELD_CHOICES).get(date_field),
        })


# --------------------------------------------------------------------------- #
# Software versions
# --------------------------------------------------------------------------- #

@register_model_view(SoftwareVersion, name='list')
class SoftwareVersionListView(ObjectListView):
    queryset = SoftwareVersion.objects.select_related('platform')
    table = tables.SoftwareVersionTable
    filterset = filtersets.SoftwareVersionFilterSet
    filterset_form = forms.SoftwareVersionFilterForm


@register_model_view(SoftwareVersion)
class SoftwareVersionView(ObjectView):
    queryset = SoftwareVersion.objects.select_related('platform')

    def get_extra_context(self, request, instance):
        devices = DeviceSoftware.objects.filter(
            software_version=instance
        ).select_related('device', 'device__site')[:100]
        standards = instance.approved_by_standards.prefetch_related(
            'device_types', 'platforms'
        )
        return {'running_on': devices, 'standards': standards}


@register_model_view(SoftwareVersion, 'edit')
class SoftwareVersionEditView(ObjectEditView):
    queryset = SoftwareVersion.objects.all()
    form = forms.SoftwareVersionForm


@register_model_view(SoftwareVersion, 'delete')
class SoftwareVersionDeleteView(ObjectDeleteView):
    queryset = SoftwareVersion.objects.all()


@register_model_view(SoftwareVersion, 'bulk_edit')
class SoftwareVersionBulkEditView(BulkEditView):
    queryset = SoftwareVersion.objects.all()
    filterset = filtersets.SoftwareVersionFilterSet
    table = tables.SoftwareVersionTable
    form = forms.SoftwareVersionBulkEditForm


@register_model_view(SoftwareVersion, 'bulk_delete')
class SoftwareVersionBulkDeleteView(BulkDeleteView):
    queryset = SoftwareVersion.objects.all()
    filterset = filtersets.SoftwareVersionFilterSet
    table = tables.SoftwareVersionTable


@register_model_view(SoftwareVersion, 'bulk_import')
class SoftwareVersionBulkImportView(BulkImportView):
    queryset = SoftwareVersion.objects.all()
    model_form = forms.SoftwareVersionImportForm


# --------------------------------------------------------------------------- #
# Software standards
# --------------------------------------------------------------------------- #

@register_model_view(SoftwareStandard, name='list')
class SoftwareStandardListView(ObjectListView):
    queryset = SoftwareStandard.objects.prefetch_related(
        'device_types', 'platforms', 'approved_versions', 'preferred_version'
    )
    table = tables.SoftwareStandardTable
    filterset = filtersets.SoftwareStandardFilterSet
    filterset_form = forms.SoftwareStandardFilterForm


@register_model_view(SoftwareStandard)
class SoftwareStandardView(ObjectView):
    queryset = SoftwareStandard.objects.prefetch_related(
        'device_types', 'platforms', 'approved_versions'
    )

    def get_extra_context(self, request, instance):
        """Show related standards — anything sharing part of this one's scope —
        so supersession is visible in place."""
        history = SoftwareStandard.objects.filter(
            Q(device_types__in=instance.device_types.all())
            | Q(platforms__in=instance.platforms.all())
        ).exclude(pk=instance.pk).distinct().order_by('-valid_from')
        return {'history': history}


@register_model_view(SoftwareStandard, 'edit')
class SoftwareStandardEditView(ObjectEditView):
    queryset = SoftwareStandard.objects.all()
    form = forms.SoftwareStandardForm


@register_model_view(SoftwareStandard, 'delete')
class SoftwareStandardDeleteView(ObjectDeleteView):
    queryset = SoftwareStandard.objects.all()


@register_model_view(SoftwareStandard, 'bulk_edit')
class SoftwareStandardBulkEditView(BulkEditView):
    queryset = SoftwareStandard.objects.all()
    filterset = filtersets.SoftwareStandardFilterSet
    table = tables.SoftwareStandardTable
    form = forms.SoftwareStandardBulkEditForm


@register_model_view(SoftwareStandard, 'bulk_delete')
class SoftwareStandardBulkDeleteView(BulkDeleteView):
    queryset = SoftwareStandard.objects.all()
    filterset = filtersets.SoftwareStandardFilterSet
    table = tables.SoftwareStandardTable


# --------------------------------------------------------------------------- #
# Per-device running software
# --------------------------------------------------------------------------- #

@register_model_view(DeviceSoftware, name='list')
class DeviceSoftwareListView(ObjectListView):
    queryset = DeviceSoftware.objects.select_related(
        'device', 'device__site', 'software_version', 'software_version__platform'
    )
    table = tables.DeviceSoftwareTable
    filterset = filtersets.DeviceSoftwareFilterSet
    filterset_form = forms.DeviceSoftwareFilterForm


@register_model_view(DeviceSoftware)
class DeviceSoftwareView(ObjectView):
    queryset = DeviceSoftware.objects.select_related(
        'device', 'software_version', 'software_version__platform'
    )


@register_model_view(DeviceSoftware, 'edit')
class DeviceSoftwareEditView(ObjectEditView):
    queryset = DeviceSoftware.objects.all()
    form = forms.DeviceSoftwareForm


@register_model_view(DeviceSoftware, 'delete')
class DeviceSoftwareDeleteView(ObjectDeleteView):
    queryset = DeviceSoftware.objects.all()


@register_model_view(DeviceSoftware, 'bulk_edit')
class DeviceSoftwareBulkEditView(BulkEditView):
    queryset = DeviceSoftware.objects.all()
    filterset = filtersets.DeviceSoftwareFilterSet
    table = tables.DeviceSoftwareTable
    form = forms.DeviceSoftwareBulkEditForm


@register_model_view(DeviceSoftware, 'bulk_delete')
class DeviceSoftwareBulkDeleteView(BulkDeleteView):
    queryset = DeviceSoftware.objects.all()
    filterset = filtersets.DeviceSoftwareFilterSet
    table = tables.DeviceSoftwareTable


@register_model_view(DeviceSoftware, 'bulk_import')
class DeviceSoftwareBulkImportView(BulkImportView):
    queryset = DeviceSoftware.objects.all()
    model_form = forms.DeviceSoftwareImportForm


class ComplianceReportView(PermissionRequiredMixin, View):
    """Which devices run approved code, which do not, and which we cannot say.

    The report iterates DEVICES, not software records, so a device nobody has
    ever scanned shows up as Unknown instead of quietly not existing. Exempt
    devices are shown as exempt rather than filtered out, for the same reason.

    Status is derived rather than stored, so status filtering happens in Python
    after the rows are built. Everything that can be pushed into SQL — site,
    role, platform, manufacturer, device type — is applied to the device
    queryset first, which is what keeps that affordable.
    """

    permission_required = 'netbox_refresh.view_devicesoftware'
    template_name = 'netbox_refresh/compliance_report.html'

    def get(self, request):
        form = forms.ComplianceReportForm(request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound else {}

        devices = Device.objects.select_related(
            'site', 'device_type', 'device_type__manufacturer', 'platform', 'role'
        )
        if data.get('site'):
            devices = devices.filter(site__in=data['site'])
        if data.get('role'):
            devices = devices.filter(role__in=data['role'])
        if data.get('platform'):
            devices = devices.filter(platform__in=data['platform'])
        if data.get('manufacturer'):
            devices = devices.filter(device_type__manufacturer__in=data['manufacturer'])
        if data.get('device_type'):
            devices = devices.filter(device_type__in=data['device_type'])

        rows = compliance.device_compliance_rows(devices, on_date=data.get('as_of'))
        summary = compliance.summarise(rows)

        wanted = data.get('status')
        if wanted:
            rows = [row for row in rows if row['status'] in wanted]

        rows.sort(key=lambda row: (row['status'], str(row['device'])))
        stale_count = sum(1 for row in rows if row['is_stale'])

        return render(request, self.template_name, {
            'form': form,
            'table': tables.ComplianceReportTable(rows),
            'summary': summary,
            'row_count': len(rows),
            'total_devices': sum(item['count'] for item in summary),
            'stale_count': stale_count,
            'as_of': data.get('as_of'),
            'filtered': bool(wanted),
        })


class VersionRollupView(PermissionRequiredMixin, View):
    """Devices per version per model — what an upgrade campaign is planned from."""

    permission_required = 'netbox_refresh.view_devicesoftware'
    template_name = 'netbox_refresh/version_rollup.html'

    def get(self, request):
        form = forms.ComplianceReportForm(request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound else {}

        devices = Device.objects.select_related(
            'site', 'device_type', 'device_type__manufacturer', 'platform'
        )
        if data.get('site'):
            devices = devices.filter(site__in=data['site'])
        if data.get('role'):
            devices = devices.filter(role__in=data['role'])
        if data.get('platform'):
            devices = devices.filter(platform__in=data['platform'])
        if data.get('manufacturer'):
            devices = devices.filter(device_type__manufacturer__in=data['manufacturer'])
        if data.get('device_type'):
            devices = devices.filter(device_type__in=data['device_type'])

        rows = compliance.device_compliance_rows(devices, on_date=data.get('as_of'))

        # Group on (device type, version). Compliance is constant within a group:
        # the standard is resolved from device type then platform, both of which
        # are fixed inside the group.
        groups = {}
        for row in rows:
            device = row['device']
            key = (str(device.device_type), str(device.platform or '—'), row['version'] or '—')
            bucket = groups.setdefault(key, {
                'device_type': key[0],
                'platform': key[1],
                'version': key[2],
                'version_url': (row['record'].software_version.get_absolute_url()
                                if row['record'] and row['record'].software_version_id else None),
                'status_label': row['status_label'],
                'status_color': row['status_color'],
                'count': 0,
            })
            bucket['count'] += 1

        table_rows = sorted(
            groups.values(), key=lambda item: (-item['count'], item['device_type'])
        )
        return render(request, self.template_name, {
            'form': form,
            'table': tables.VersionRollupTable(table_rows),
            'group_count': len(table_rows),
            'total_devices': sum(item['count'] for item in table_rows),
        })


class CiscoSyncView(PermissionRequiredMixin, View):
    """Kick off the Cisco EoX sync as a background job."""

    permission_required = 'netbox_refresh.change_modellifecycle'

    def post(self, request):
        from netbox_refresh.jobs import CiscoEoxSyncJob

        try:
            CiscoEoxSyncJob.enqueue(user=request.user)
        except Exception as exc:  # noqa: BLE001 - surface scheduling failures in the UI
            messages.error(request, 'Could not start the Cisco EoX sync: %s' % exc)
        else:
            messages.success(
                request,
                'Cisco EoX sync started. Progress is under Operations › Jobs.',
            )
        return redirect('plugins:netbox_refresh:modellifecycle_list')
