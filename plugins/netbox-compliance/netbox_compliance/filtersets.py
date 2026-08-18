"""REST and UI filters.

Two filters here are worth a note because they cannot be plain field lookups:

  `status` on ConfigCompliance folds exemption over the stored result, which is
  a Python-side rule. It is implemented as a queryset filter anyway — exempt is
  a column and the review date is a column, so the fold is expressible in SQL —
  because a status filter that only worked on the current page would be worse
  than none.

  `stale` compares last_checked against a plugin setting, so the threshold is
  computed per request rather than baked into the filterset at import time.
"""

from datetime import date, timedelta

import django_filters
from dcim.models import Device, DeviceRole, Platform, Site
from django.db.models import Q
from django.utils import timezone
from extras.models import Tag
from netbox.filtersets import NetBoxModelFilterSet

from netbox_compliance.choices import (
    ConfigCheckResultChoices,
    ConfigCheckSourceChoices,
    ConfigCheckTypeChoices,
    ConfigComplianceStatusChoices,
)
from netbox_compliance.models import ConfigCompliance, ConfigStandard

__all__ = (
    'ConfigStandardFilterSet',
    'ConfigComplianceFilterSet',
)


class ConfigStandardFilterSet(NetBoxModelFilterSet):
    check_type = django_filters.MultipleChoiceFilter(choices=ConfigCheckTypeChoices)
    platform_id = django_filters.ModelMultipleChoiceFilter(
        field_name='platforms', queryset=Platform.objects.all(), label='Platform',
    )
    role_id = django_filters.ModelMultipleChoiceFilter(
        field_name='roles', queryset=DeviceRole.objects.all(), label='Device role',
    )
    site_id = django_filters.ModelMultipleChoiceFilter(
        field_name='sites', queryset=Site.objects.all(), label='Site',
    )
    device_tag_id = django_filters.ModelMultipleChoiceFilter(
        field_name='device_tags', queryset=Tag.objects.all(), label='Device tag',
    )
    active = django_filters.BooleanFilter(
        method='filter_active', label='In force today',
    )
    # The checker asks for exactly this: everything it is allowed to act on.
    device_id = django_filters.ModelChoiceFilter(
        queryset=Device.objects.all(), method='filter_device',
        label='Applies to device',
    )

    class Meta:
        model = ConfigStandard
        fields = {
            'id': ['exact'],
            'name': ['exact', 'icontains'],
            'auto_remediable': ['exact'],
            'allow_enforce': ['exact'],
            'valid_from': ['exact', 'gte', 'lte'],
            'valid_to': ['exact', 'gte', 'lte'],
        }

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(name__icontains=value)
            | Q(match_pattern__icontains=value)
            | Q(description__icontains=value)
            | Q(comments__icontains=value)
        )

    def filter_active(self, queryset, name, value):
        today = date.today()
        in_force = Q(valid_from__lte=today) & (Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        return queryset.filter(in_force) if value else queryset.exclude(in_force)

    def filter_device(self, queryset, name, value):
        """Standards in scope for one device — empty scope means no restriction.

        Written as four OR-ed conditions rather than in Python because the
        checker calls this for every device it visits, and because the same
        rule then holds for the API, the UI filter and the report.
        """
        if value is None:
            return queryset
        device_tag_ids = list(value.tags.values_list('pk', flat=True))
        tag_clause = Q(device_tags__isnull=True)
        if device_tag_ids:
            tag_clause |= Q(device_tags__in=device_tag_ids)
        return queryset.filter(
            Q(platforms__isnull=True) | Q(platforms=value.platform_id),
        ).filter(
            Q(roles__isnull=True) | Q(roles=value.role_id),
        ).filter(
            Q(sites__isnull=True) | Q(sites=value.site_id),
        ).filter(tag_clause).distinct()


class ConfigComplianceFilterSet(NetBoxModelFilterSet):
    device_id = django_filters.ModelMultipleChoiceFilter(
        field_name='device', queryset=Device.objects.all(), label='Device',
    )
    standard_id = django_filters.ModelMultipleChoiceFilter(
        field_name='standard', queryset=ConfigStandard.objects.all(), label='Standard',
    )
    site_id = django_filters.ModelMultipleChoiceFilter(
        field_name='device__site', queryset=Site.objects.all(), label='Site',
    )
    platform_id = django_filters.ModelMultipleChoiceFilter(
        field_name='device__platform', queryset=Platform.objects.all(), label='Platform',
    )
    role_id = django_filters.ModelMultipleChoiceFilter(
        field_name='device__role', queryset=DeviceRole.objects.all(), label='Device role',
    )
    result = django_filters.MultipleChoiceFilter(choices=ConfigCheckResultChoices)
    source = django_filters.MultipleChoiceFilter(choices=ConfigCheckSourceChoices)
    status = django_filters.MultipleChoiceFilter(
        choices=ConfigComplianceStatusChoices, method='filter_status',
        label='Compliance status',
    )
    stale = django_filters.BooleanFilter(method='filter_stale', label='Result is stale')
    needs_manual_fix = django_filters.BooleanFilter(
        method='filter_manual', label='Needs manual remediation',
    )

    class Meta:
        model = ConfigCompliance
        fields = {
            'id': ['exact'],
            'exempt': ['exact'],
            'last_checked': ['gte', 'lte'],
            'exempt_review_by': ['exact', 'gte', 'lte'],
        }

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(device__name__icontains=value)
            | Q(standard__name__icontains=value)
            | Q(observed__icontains=value)
            | Q(error_message__icontains=value)
            | Q(exempt_reason__icontains=value)
        )

    def filter_status(self, queryset, name, value):
        """Exemption wins over the stored result, exactly as the property does."""
        today = date.today()
        expired = Q(exempt=True, exempt_review_by__isnull=False, exempt_review_by__lt=today)
        current_exemption = Q(exempt=True) & ~expired

        clause = Q()
        for status in value:
            if status == ConfigComplianceStatusChoices.STATUS_EXEMPT:
                clause |= current_exemption
            elif status == ConfigComplianceStatusChoices.STATUS_EXEMPT_EXPIRED:
                clause |= expired
            else:
                clause |= Q(exempt=False, result=status)
        return queryset.filter(clause) if clause else queryset

    def filter_stale(self, queryset, name, value):
        from django.conf import settings

        days = settings.PLUGINS_CONFIG.get('netbox_compliance', {}).get('stale_after_days', 30)
        threshold = timezone.now() - timedelta(days=days)
        # Never-checked rows are Not checked, not stale — the two say different
        # things and the report shows them differently.
        checked = ~Q(result=ConfigCheckResultChoices.RESULT_UNKNOWN)
        old = Q(last_checked__lt=threshold) | Q(last_checked__isnull=True)
        return queryset.filter(checked & old) if value else queryset.exclude(checked & old)

    def filter_manual(self, queryset, name, value):
        clause = Q(
            result=ConfigCheckResultChoices.RESULT_NON_COMPLIANT,
            exempt=False,
            standard__auto_remediable=False,
        )
        return queryset.filter(clause) if value else queryset.exclude(clause)
