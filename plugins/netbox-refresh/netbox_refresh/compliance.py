"""Deciding whether a device is running approved code.

Compliance here is set membership, never comparison: a device is compliant if
the version it is running is one of the versions its standard explicitly
approves. There is no "at or above" mode and no version-ordering code anywhere
in this plugin, which is a deliberate choice — Cisco IOS 15.2(4)E10, IOS-XE
16.12.05, NX-OS 9.3(10), PAN-OS 10.2.9-h1 and ArubaOS 8.10.0.10 do not sort
lexically, per-vendor comparators are where this kind of tooling usually goes
quietly wrong, and an estate normally has two or three blessed versions at once
anyway rather than a floor.

The one thing that must not happen is a device reading as compliant when we
simply do not know what it runs, so "no version collected" and "no standard
defined" are their own states rather than being folded into pass or fail.
"""

from datetime import date

from django.db.models import Q

from netbox_refresh.choices import ComplianceStatusChoices
from netbox_refresh.models import DeviceSoftware, SoftwareStandard

__all__ = (
    'StandardResolver',
    'standard_for_device',
    'evaluate',
    'device_compliance_rows',
)


def active_standards(on_date=None, queryset=None):
    """Standards in force on a given day."""
    on_date = on_date or date.today()
    queryset = SoftwareStandard.objects.all() if queryset is None else queryset
    return queryset.filter(valid_from__lte=on_date).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=on_date)
    )


class StandardResolver:
    """Maps devices to the standard that applies to them, in two queries total.

    A device-type standard beats a platform standard: the platform rule is the
    broad one ("all IOS-XE runs 17.09.04a") and the device-type rule is the
    override ("but 9500s run 17.12.03").

    The compliance report renders a row per device, so resolving per device
    would be two queries each. Everything is loaded once and kept in dicts —
    safe because the overlap check in SoftwareStandard.clean() guarantees at
    most one standard per scope is in force on any given day.
    """

    def __init__(self, on_date=None):
        self.on_date = on_date or date.today()
        self._by_device_type = {}
        self._by_platform = {}
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        standards = active_standards(
            self.on_date,
            SoftwareStandard.objects.prefetch_related(
                'approved_versions', 'device_types', 'platforms'
            ),
        )
        # A standard now covers many scopes; index it under each one. The
        # overlap validation guarantees no two active standards share a scope,
        # so each key still maps to exactly one standard.
        for standard in standards:
            for device_type in standard.device_types.all():
                self._by_device_type[device_type.pk] = standard
            for platform in standard.platforms.all():
                self._by_platform[platform.pk] = standard
        self._loaded = True

    def for_device(self, device):
        if device is None:
            return None
        self._load()
        found = self._by_device_type.get(device.device_type_id)
        if found is not None:
            return found
        if device.platform_id:
            return self._by_platform.get(device.platform_id)
        return None


def standard_for_device(device, on_date=None):
    """The standard applying to one device. Prefer StandardResolver in bulk."""
    if device is None:
        return None
    on_date = on_date or date.today()
    base = SoftwareStandard.objects.prefetch_related('approved_versions')

    found = active_standards(on_date, base).filter(
        device_types=device.device_type_id
    ).first()
    if found is not None:
        return found

    if device.platform_id:
        return active_standards(on_date, base).filter(
            platforms=device.platform_id
        ).first()
    return None


def evaluate(device_software, standard=None, resolver=None):
    """The compliance state of one DeviceSoftware row.

    Order matters. Exemption is checked first because an exempt device is
    excluded from the pass/fail question entirely — but it is still reported,
    as its own state, never dropped from the view.
    """
    if device_software is None:
        return ComplianceStatusChoices.STATUS_UNKNOWN

    if device_software.exempt:
        if device_software.exemption_expired:
            return ComplianceStatusChoices.STATUS_EXEMPT_EXPIRED
        return ComplianceStatusChoices.STATUS_EXEMPT

    # A raw_version we could not match to a catalogued version counts as
    # Unknown, not as non-compliant: we know the device said something, but not
    # what it means in terms of our approved set. The row still shows the raw
    # string so somebody can go and catalogue it.
    if not device_software.software_version_id:
        return ComplianceStatusChoices.STATUS_UNKNOWN

    if standard is None:
        standard = (
            resolver.for_device(device_software.device) if resolver is not None
            else standard_for_device(device_software.device)
        )
    if standard is None:
        return ComplianceStatusChoices.STATUS_NO_STANDARD

    # .all() rather than .filter().exists() so a prefetched standard costs no
    # extra query — the report prefetches, the detail pages do not.
    approved = {version.pk for version in standard.approved_versions.all()}
    if device_software.software_version_id in approved:
        return ComplianceStatusChoices.STATUS_COMPLIANT
    return ComplianceStatusChoices.STATUS_NON_COMPLIANT


def device_compliance_rows(devices, on_date=None):
    """Compliance for a queryset of devices, including ones we know nothing about.

    Iterating devices rather than DeviceSoftware records is the whole point: a
    device with no software record at all is exactly the device most likely to
    be missed, and it has to appear as Unknown rather than vanish from the
    report. Returns dicts, one per device, ready for the report table.
    """
    resolver = StandardResolver(on_date)
    devices = list(devices)

    records = DeviceSoftware.objects.filter(
        device__in=[d.pk for d in devices]
    ).select_related('software_version', 'software_version__platform')
    by_device = {record.device_id: record for record in records}

    rows = []
    for device in devices:
        record = by_device.get(device.pk)
        if record is not None:
            # The resolver needs the device object; reuse the one we already
            # have rather than letting the FK re-fetch it.
            record.device = device
        standard = resolver.for_device(device)
        status = evaluate(record, standard=standard)
        rows.append({
            'device': device,
            'record': record,
            'standard': standard,
            'status': status,
            'status_label': _label(status),
            'status_color': ComplianceStatusChoices.colors.get(status),
            'version': record.version_label if record else '',
            'source': record.get_source_display() if record else '',
            'as_of': record.as_of if record else None,
            'is_stale': record.is_stale if record else False,
            'approved': ', '.join(
                str(v.version) for v in standard.approved_versions.all()
            ) if standard else '',
        })
    return rows


def summarise(rows):
    """Count rows per compliance state, in the order the choices declare them."""
    counts = {value: 0 for value, _label, _color in ComplianceStatusChoices.CHOICES}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return [
        {
            'status': value,
            'label': label,
            'color': color,
            'count': counts.get(value, 0),
        }
        for value, label, color in ComplianceStatusChoices.CHOICES
    ]


def _label(status):
    labels = {entry[0]: entry[1] for entry in ComplianceStatusChoices.CHOICES}
    return labels.get(status, status)
