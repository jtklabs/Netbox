"""Overrides a reviewer supplied, applied to the reading the poller holds.

The plugin half of this lives in the plugin's own suite; this is the scanner
side, which is where the override actually changes what gets written.
"""

from __future__ import annotations

from snmpinv.model import DeviceRecord, ScanResult
from snmpinv.onboarding import _apply_overrides


def result_with(model: str) -> ScanResult:
    return ScanResult(host="198.51.100.10", devices=[
        DeviceRecord(name="fw-dal-01", model=model, serial="JAD12345678",
                     manufacturer="Cisco"),
    ])


class TestTheModelOverride:
    def test_it_fills_in_a_model_the_device_did_not_report(self):
        """A Firepower 2120 publishes none, so this is the only way it can be
        created at all."""
        result = result_with("")
        _apply_overrides(result, {"override_model": "FPR-2120"})
        assert result.primary.model == "FPR-2120"

    def test_what_the_device_reported_still_wins(self):
        """An override left on a request must not quietly replace a model a
        later scan managed to read."""
        result = result_with("FPR-2130")
        _apply_overrides(result, {"override_model": "FPR-2120"})
        assert result.primary.model == "FPR-2130"

    def test_no_override_changes_nothing(self):
        result = result_with("")
        _apply_overrides(result, {})
        assert result.primary.model == ""

    def test_whitespace_is_not_a_model(self):
        result = result_with("")
        _apply_overrides(result, {"override_model": "   "})
        assert result.primary.model == ""

    def test_it_leaves_the_rest_of_the_reading_alone(self):
        """The whole reason for an override rather than typing the device by
        hand: the scan was fine apart from this one field."""
        result = result_with("")
        _apply_overrides(result, {"override_model": "FPR-2120"})
        assert result.primary.serial == "JAD12345678"
        assert result.primary.manufacturer == "Cisco"

    def test_it_works_alongside_a_name_override(self):
        result = result_with("")
        _apply_overrides(result, {"override_model": "FPR-2120",
                                  "override_name": "fw-dal-99"})
        assert result.primary.model == "FPR-2120"
        assert result.primary.name == "fw-dal-99"
