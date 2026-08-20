"""Support-coverage reporting: who is covered, by whom, until when.

Both reports answer device-level questions, but coverage is recorded on quote
LINES, which may be matched to a device directly or to one of its modules or
inventory items. A device's effective coverage is the latest coverage_end
across every line that ultimately points at it — a renewal already entered as
a later line means the earlier expiry no longer matters.

Only quotes in a coverage-bearing status count. A quote we are still
reviewing is an offer, not coverage; an expired or superseded quote was never
acted on. Accepted and ordered are the two states that mean money was (or is
being) spent on the coverage the lines describe.
"""

from dataclasses import dataclass
from datetime import date

from dcim.models import Device, InventoryItem, Module
from django.contrib.contenttypes.prefetch import GenericPrefetch

from netbox_quotes.choices import QuoteStatusChoices
from netbox_quotes.models import QuoteLine

__all__ = (
    'COVERAGE_BEARING_STATUSES',
    'DeviceCoverage',
    'device_coverage_map',
)

COVERAGE_BEARING_STATUSES = (
    QuoteStatusChoices.STATUS_ACCEPTED,
    QuoteStatusChoices.STATUS_ORDERED,
)


@dataclass
class DeviceCoverage:
    """The support coverage one device effectively has."""

    device: Device
    coverage_end: date
    line: QuoteLine

    @property
    def vendor(self):
        return self.line.quote.vendor

    @property
    def quote(self):
        return self.line.quote

    @property
    def days_left(self) -> int:
        """Negative once the coverage has lapsed."""
        return (self.coverage_end - date.today()).days

    @property
    def is_third_party(self) -> bool:
        return self.line.quote.vendor.is_third_party_maintenance


def _coverage_lines(device_pks=None):
    lines = QuoteLine.objects.filter(
        quote__status__in=COVERAGE_BEARING_STATUSES,
        coverage_end__isnull=False,
        assigned_object_id__isnull=False,
    )
    if device_pks is not None:
        lines = lines.for_devices(device_pks)
    # GenericPrefetch so resolving a module or inventory-item line to its
    # parent device does not cost a query per line.
    return lines.select_related('quote__vendor').prefetch_related(
        GenericPrefetch('assigned_object', [
            Device.objects.select_related('site', 'device_type', 'device_type__manufacturer'),
            Module.objects.select_related(
                'device__site', 'device__device_type', 'device__device_type__manufacturer'
            ),
            InventoryItem.objects.select_related(
                'device__site', 'device__device_type', 'device__device_type__manufacturer'
            ),
        ])
    )


def device_coverage_map(device_pks=None):
    """Effective coverage per device: {device_id: DeviceCoverage}.

    Restricted to the given devices when device_pks is passed; the whole
    covered estate otherwise. Devices with no coverage-bearing lines simply
    have no entry — absence is the answer, and the EoL report treats it as
    its most urgent state.
    """
    best: dict[int, DeviceCoverage] = {}
    for line in _coverage_lines(device_pks):
        device = line.device
        if device is None:
            # The assigned object was deleted between matching and now.
            continue
        current = best.get(device.pk)
        if current is None or line.coverage_end > current.coverage_end:
            best[device.pk] = DeviceCoverage(
                device=device, coverage_end=line.coverage_end, line=line
            )
    return best
