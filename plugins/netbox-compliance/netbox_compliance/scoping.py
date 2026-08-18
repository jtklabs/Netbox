"""Which standards apply to which devices, and what the fleet looks like as a result.

Two jobs live here, and they have to agree with each other or the reporting is
nonsense:

  resolution  given a device, which standards is it measured against? Every
              populated scope dimension on a standard (platform, role, site,
              device tag) narrows; an empty one does not restrict. A standard
              with all four empty applies to the whole fleet.
  reporting   given a set of devices, the per-device rows and the per-standard
              rollup that answer "how many devices are missing X" — which is
              the question this plugin exists for.

The resolution is done in Python against prefetched scope lists rather than as
a queryset filter. It looks like it should be a filter, and it cannot be: "empty
means everything" is not expressible as an AND of joins, and doing it per device
would be four queries per row on a report that renders one row per device.

The most important rule in this module is that a device in scope with no result
row still appears, as Not checked. A fleet report that only lists devices
somebody has already scanned is a report that gets greener the less work you do.
"""

from datetime import date

from django.db.models import Q

from netbox_compliance.choices import (
    ConfigCheckResultChoices,
    ConfigComplianceStatusChoices,
)
from netbox_compliance.models import ConfigCompliance, ConfigStandard

__all__ = (
    'active_standards',
    'StandardResolver',
    'standards_for_device',
    'device_standard_rows',
    'summarise',
    'standard_rollup',
)


def active_standards(on_date=None, queryset=None):
    """Standards in force on a given day."""
    on_date = on_date or date.today()
    queryset = ConfigStandard.objects.all() if queryset is None else queryset
    return queryset.filter(valid_from__lte=on_date).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=on_date)
    )


class StandardResolver:
    """Maps devices to their standards, loading the standards exactly once.

    Scope membership is precomputed into sets of ids per standard, so resolving
    a device is set arithmetic rather than queries. Device tags are only looked
    at when some standard actually scopes by tag — otherwise touching
    `device.tags` would trigger a query per device for a dimension nobody used.
    """

    def __init__(self, on_date=None, standards=None):
        self.on_date = on_date or date.today()
        self._standards = standards
        self._scopes = None

    def _load(self):
        if self._scopes is not None:
            return
        if self._standards is None:
            self._standards = list(
                active_standards(self.on_date).prefetch_related(
                    'platforms', 'roles', 'sites', 'device_tags'
                )
            )
        self._scopes = [
            (
                standard,
                {p.pk for p in standard.platforms.all()},
                {r.pk for r in standard.roles.all()},
                {s.pk for s in standard.sites.all()},
                {t.pk for t in standard.device_tags.all()},
            )
            for standard in self._standards
        ]

    @property
    def standards(self):
        self._load()
        return list(self._standards)

    @property
    def uses_device_tags(self):
        self._load()
        return any(tag_ids for *_rest, tag_ids in self._scopes)

    def for_device(self, device):
        """Every standard in force that this device is in scope for."""
        if device is None:
            return []
        self._load()
        device_tag_ids = None
        matched = []
        for standard, platform_ids, role_ids, site_ids, tag_ids in self._scopes:
            if platform_ids and device.platform_id not in platform_ids:
                continue
            if role_ids and device.role_id not in role_ids:
                continue
            if site_ids and device.site_id not in site_ids:
                continue
            if tag_ids:
                if device_tag_ids is None:
                    device_tag_ids = {tag.pk for tag in device.tags.all()}
                if not tag_ids & device_tag_ids:
                    continue
            matched.append(standard)
        return matched


def standards_for_device(device, on_date=None):
    """The standards applying to one device. Prefer StandardResolver in bulk."""
    return StandardResolver(on_date).for_device(device)


def device_standard_rows(devices, on_date=None, standards=None):
    """One row per (device, standard-in-scope) pair, whether or not it was checked.

    Rows are plain dicts because the report table mixes recorded results with
    pairs that have no row in the database at all — a device in scope for a
    standard nobody has run against it is the single most important thing this
    report has to show, and it has no model instance to render.
    """
    resolver = StandardResolver(on_date, standards=standards)
    devices = list(devices)

    records = ConfigCompliance.objects.filter(
        device__in=[d.pk for d in devices]
    ).select_related('standard')
    by_pair = {(record.device_id, record.standard_id): record for record in records}

    rows = []
    for device in devices:
        for standard in resolver.for_device(device):
            record = by_pair.get((device.pk, standard.pk))
            if record is not None:
                # Reuse the objects already in hand rather than letting the FKs
                # re-fetch them once per row.
                record.device = device
                record.standard = standard
                status = record.status
            else:
                status = ConfigCheckResultChoices.RESULT_UNKNOWN
            rows.append({
                'device': device,
                'standard': standard,
                'record': record,
                'status': status,
                'status_label': _label(status),
                'status_color': ConfigComplianceStatusChoices.colors.get(status),
                'findings': record.finding_count if record else 0,
                'last_checked': record.last_checked if record else None,
                'is_stale': record.is_stale if record else False,
                'needs_manual_fix': record.needs_manual_fix if record else False,
            })
    return rows


def summarise(rows):
    """Count rows per status, in the order the choices declare them."""
    counts = {value: 0 for value, _label, _color in ConfigComplianceStatusChoices.CHOICES}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return [
        {'status': value, 'label': label, 'color': color, 'count': counts.get(value, 0)}
        for value, label, color in ConfigComplianceStatusChoices.CHOICES
    ]


def standard_rollup(rows):
    """Per-standard totals — the "how many devices are missing X" view.

    Built from the same rows as the per-device table so the two can never
    disagree about the fleet, which is exactly the failure mode of computing a
    summary with its own query.
    """
    by_standard = {}
    for row in rows:
        standard = row['standard']
        bucket = by_standard.setdefault(standard.pk, {
            'standard': standard,
            'in_scope': 0,
            'compliant': 0,
            'non_compliant': 0,
            'unknown': 0,
            'error': 0,
            'exempt': 0,
        })
        bucket['in_scope'] += 1
        status = row['status']
        if status == ConfigComplianceStatusChoices.STATUS_COMPLIANT:
            bucket['compliant'] += 1
        elif status == ConfigComplianceStatusChoices.STATUS_NON_COMPLIANT:
            bucket['non_compliant'] += 1
        elif status == ConfigComplianceStatusChoices.STATUS_ERROR:
            bucket['error'] += 1
        elif status in (
            ConfigComplianceStatusChoices.STATUS_EXEMPT,
            ConfigComplianceStatusChoices.STATUS_EXEMPT_EXPIRED,
        ):
            bucket['exempt'] += 1
        else:
            bucket['unknown'] += 1

    rollup = sorted(by_standard.values(), key=lambda item: item['standard'].name)
    for bucket in rollup:
        # Measured against what was actually checked, not against everything in
        # scope. A standard checked on four of a thousand devices and passing on
        # all four is 100% of nothing; `coverage` is what stops that reading as
        # a pass, so both numbers are always shown together.
        checked = bucket['compliant'] + bucket['non_compliant'] + bucket['error']
        bucket['checked'] = checked
        bucket['coverage'] = _percent(checked, bucket['in_scope'])
        bucket['compliance'] = _percent(bucket['compliant'], checked)
    return rollup


def _percent(part, whole):
    if not whole:
        return None
    return round(100.0 * part / whole, 1)


def _label(status):
    labels = {entry[0]: entry[1] for entry in ConfigComplianceStatusChoices.CHOICES}
    return labels.get(status, status)
