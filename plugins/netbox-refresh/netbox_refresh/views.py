from collections import defaultdict

from dcim.models import Device, DeviceType, Module, ModuleType
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Count
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

from netbox_refresh import filtersets, forms, tables
from netbox_refresh.models import ModelLifecycle


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


class RefreshReportView(PermissionRequiredMixin, View):
    """Hardware reaching a lifecycle milestone in a window, with replacement cost.

    Installed counts are gathered with two aggregate queries rather than one per
    model, so the report stays cheap as the estate grows.
    """

    permission_required = 'netbox_refresh.view_modellifecycle'
    template_name = 'netbox_refresh/refresh_report.html'

    def get(self, request):
        form = forms.RefreshReportForm(request.GET or None)
        form.is_valid()
        data = form.cleaned_data if form.is_bound else {}

        date_field = data.get('date_field') or 'end_of_support'
        after = data.get('after')
        before = data.get('before')
        manufacturers = data.get('manufacturer')
        sites = data.get('site')

        queryset = ModelLifecycle.objects.prefetch_related(
            'assigned_object_type', 'replacement_device_type', 'replacement_module_type'
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

        device_counts = defaultdict(int)
        if device_type_ids:
            device_qs = Device.objects.filter(device_type_id__in=device_type_ids)
            if sites:
                device_qs = device_qs.filter(site__in=sites)
            for row in device_qs.values('device_type_id').annotate(n=Count('pk')):
                device_counts[row['device_type_id']] = row['n']

        module_counts = defaultdict(int)
        if module_type_ids:
            module_qs = Module.objects.filter(module_type_id__in=module_type_ids)
            if sites:
                module_qs = module_qs.filter(device__site__in=sites)
            for row in module_qs.values('module_type_id').annotate(n=Count('pk')):
                module_counts[row['module_type_id']] = row['n']

        type_cache = {
            'devicetype': DeviceType.objects.in_bulk(device_type_ids),
            'moduletype': ModuleType.objects.in_bulk(module_type_ids),
        }

        rows = []
        totals = defaultdict(lambda: 0)
        total_units = 0
        missing_cost = 0
        for record in records:
            model_name = record.assigned_object_type.model
            obj = type_cache.get(model_name, {}).get(record.assigned_object_id)
            if obj is None:
                continue
            if manufacturers and obj.manufacturer_id not in {m.pk for m in manufacturers}:
                continue
            installed = (device_counts if model_name == 'devicetype' else module_counts)[obj.pk]
            if sites and not installed:
                continue  # nothing of this model at the selected sites

            unit_cost = record.replacement_cost
            extended = unit_cost * installed if unit_cost is not None else None
            if unit_cost is None:
                missing_cost += 1
            else:
                totals[record.currency] += extended
            total_units += installed

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
                'unit_cost': unit_cost if unit_cost is not None else '—',
                'extended_cost': extended if extended is not None else '—',
            })

        rows.sort(key=lambda r: (r['milestone_date'] is None, r['milestone_date']))
        table = tables.RefreshReportTable(rows)

        return render(request, self.template_name, {
            'form': form,
            'table': table,
            'row_count': len(rows),
            'total_units': total_units,
            'totals': dict(totals),
            'missing_cost': missing_cost,
            'date_label': dict(forms.RefreshReportForm.DATE_FIELD_CHOICES).get(date_field),
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
