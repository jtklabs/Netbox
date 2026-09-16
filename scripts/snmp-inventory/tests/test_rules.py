"""Discovery rules: filling in what a device did not report.

The plugin stores the rules; this is the half that applies them, which is
where a rule actually changes what gets written. Nothing here needs a NetBox
— a rule is a dataclass and a scan result is built by hand.
"""

from __future__ import annotations

import logging

import pytest
from conftest import collect_fixture

from snmpinv import rules as rules_module
from snmpinv.collect import DeviceFacts
from snmpinv.model import DeviceRecord, ScanResult, build_scan_result
from snmpinv.netbox import NetBoxError
from snmpinv.onboarding import _hardware_changed, scan_payload
from snmpinv.rules import Rule, apply_rules, load_rules, rule_from_api


def rule(**overrides) -> Rule:
    """The rule from the feature request: name contains X, model blank -> Y."""
    values = dict(name="Firepower by name", match_field="name",
                  match_operator="contains", match_value="fw-",
                  set_field="model", set_value="FPR-2120")
    values.update(overrides)
    return Rule(**values)


def result_for(name="fw-dal-01", model="", **fields) -> ScanResult:
    facts = DeviceFacts(host="198.51.100.10")
    facts.sys_descr = fields.pop("sys_descr", "Cisco Firepower Threat Defense")
    record = dict(name=name, model=model, serial="JAD12345678", manufacturer="Cisco")
    record.update(fields)
    return ScanResult(host="198.51.100.10", facts=facts, devices=[DeviceRecord(**record)])


class TestTheExampleFromTheRequest:
    def test_a_blank_model_is_filled_in(self):
        result = result_for()
        applied = apply_rules(result, [rule()])
        assert result.primary.model == "FPR-2120"
        assert [(a.field, a.value, a.previous) for a in applied] == [("model", "FPR-2120", "")]

    def test_what_the_device_reported_wins(self):
        """The same promise the reviewer's override makes: a rule fills gaps,
        it does not overrule a device that answered."""
        result = result_for(model="FPR-2130")
        assert apply_rules(result, [rule()]) == []
        assert result.primary.model == "FPR-2130"

    def test_a_name_that_does_not_match_is_left_alone(self):
        result = result_for(name="core-sw-01")
        assert apply_rules(result, [rule()]) == []
        assert result.primary.model == ""

    def test_the_rest_of_the_reading_is_untouched(self):
        result = result_for()
        apply_rules(result, [rule()])
        assert result.primary.serial == "JAD12345678"
        assert result.primary.manufacturer == "Cisco"

    def test_the_result_remembers_what_the_rule_supplied(self):
        """So the review page can show which facts came from the device."""
        result = result_for()
        apply_rules(result, [rule()])
        assert result.rules_applied[0].rule == "Firepower by name"
        assert result.rules_applied[0].device == "fw-dal-01"


class TestReplacingIsExplicit:
    def test_only_if_blank_off_overrides_the_device(self):
        result = result_for(model="FPR-2130")
        applied = apply_rules(result, [rule(only_if_blank=False)])
        assert result.primary.model == "FPR-2120"
        # And the value it replaced is kept, so the substitution is visible.
        assert applied[0].previous == "FPR-2130"

    def test_a_device_that_already_says_so_records_nothing(self):
        result = result_for(model="FPR-2120")
        assert apply_rules(result, [rule(only_if_blank=False)]) == []


class TestOperators:
    @pytest.mark.parametrize("operator, value, name, expected", [
        ("contains", "FW-", "fw-dal-01", True),         # case-insensitive
        ("contains", "dal", "fw-dal-01", True),
        ("contains", "nyc", "fw-dal-01", False),
        ("starts_with", "fw-", "fw-dal-01", True),
        ("starts_with", "dal", "fw-dal-01", False),
        ("ends_with", "-01", "fw-dal-01", True),
        ("ends_with", "-02", "fw-dal-01", False),
        ("equals", "FW-DAL-01", "fw-dal-01", True),
        ("equals", "fw-dal", "fw-dal-01", False),
        ("regex", r"^fw-[a-z]{3}-\d+$", "fw-dal-01", True),
        ("regex", r"^sw-", "fw-dal-01", False),
    ])
    def test_matching(self, operator, value, name, expected):
        result = result_for(name=name)
        apply_rules(result, [rule(match_operator=operator, match_value=value)])
        assert (result.primary.model == "FPR-2120") is expected

    def test_a_regex_that_will_not_compile_never_matches(self):
        """The plugin refuses these on save; an older NetBox might not."""
        result = result_for()
        apply_rules(result, [rule(match_operator="regex", match_value="(")])
        assert result.primary.model == ""


class TestWhatCanBeMatched:
    def test_sysdescr_names_the_product_family_on_most_platforms(self):
        result = result_for(name="whatever", sys_descr="Cisco Firepower Threat Defense")
        apply_rules(result, [rule(match_field="sys_descr", match_value="firepower")])
        assert result.primary.model == "FPR-2120"

    def test_the_scanned_address(self):
        result = result_for(name="whatever")
        apply_rules(result, [rule(match_field="address", match_operator="starts_with",
                                  match_value="198.51.100.")])
        assert result.primary.model == "FPR-2120"

    def test_the_manufacturer(self):
        result = result_for(name="whatever")
        apply_rules(result, [rule(match_field="manufacturer", match_operator="equals",
                                  match_value="cisco")])
        assert result.primary.model == "FPR-2120"

    def test_a_result_with_no_facts_still_matches_on_the_device(self):
        """A result rebuilt from a stored preview carries no facts."""
        result = ScanResult(host="198.51.100.10", devices=[DeviceRecord(name="fw-dal-01")])
        apply_rules(result, [rule()])
        assert result.primary.model == "FPR-2120"


class TestWhatCanBeSet:
    @pytest.mark.parametrize("field", ["model", "manufacturer", "platform", "software_version"])
    def test_each_target(self, field):
        result = result_for(manufacturer="")
        apply_rules(result, [rule(set_field=field, set_value="X")])
        assert getattr(result.primary, field) == "X"

    def test_a_serial_the_device_does_not_report(self):
        """A VM appliance or a box with a blank ENTITY-MIB. One rule per box,
        pinned to it by name."""
        result = result_for(serial="")
        applied = apply_rules(result, [rule(
            name="fw-dal-01 serial", match_operator="equals", match_value="fw-dal-01",
            set_field="serial", set_value="FAZ-VMTM12345678",
        )])
        assert result.primary.serial == "FAZ-VMTM12345678"
        assert applied[0].field == "serial"

    def test_a_serial_the_device_reports_is_never_replaced(self):
        result = result_for()
        apply_rules(result, [rule(match_operator="equals", match_value="fw-dal-01",
                                  set_field="serial", set_value="OTHER")])
        assert result.primary.serial == "JAD12345678"


class TestSerialRulesArePinnedToOneBox:
    """The plugin refuses these on save; the poller refuses them again so an
    older NetBox cannot hand it a rule that stamps one serial on a fleet."""

    def serial_rule(self, **overrides):
        values = dict(match_field="name", match_operator="equals", match_value="fw-dal-01",
                      set_field="serial", set_value="JAD12345678")
        values.update(overrides)
        return rule(**values)

    @pytest.mark.parametrize("match_field", ["name", "address"])
    def test_exact_name_or_address_is_allowed(self, match_field):
        assert rules_module.validate_rule(self.serial_rule(match_field=match_field)) == ""

    @pytest.mark.parametrize("broken", [
        {"match_operator": "contains"},
        {"match_operator": "starts_with"},
        {"match_operator": "regex", "match_value": "^fw-dal-01$"},
        {"match_field": "sys_descr"},
        {"match_field": "manufacturer"},
    ])
    def test_anything_looser_is_refused(self, broken):
        assert "match the device name or address exactly" in \
            rules_module.validate_rule(self.serial_rule(**broken))

    def test_replacing_a_reported_serial_is_refused(self):
        """A changed serial is how a hardware swap is detected; a rule must
        not be able to fake one."""
        assert "never replace one" in \
            rules_module.validate_rule(self.serial_rule(only_if_blank=False))


class TestOrderAndChaining:
    def test_rules_are_applied_in_the_order_given(self):
        first = rule(name="first", set_value="FIRST")
        second = rule(name="second", set_value="SECOND")
        result = result_for()
        applied = apply_rules(result, [first, second])
        assert result.primary.model == "FIRST"
        assert [a.rule for a in applied] == ["first"]

    def test_a_later_rule_sees_what_an_earlier_one_set(self):
        """Manufacturer from the name, then platform from the manufacturer."""
        by_name = rule(name="vendor", set_field="manufacturer", set_value="Cisco")
        by_vendor = rule(name="os", match_field="manufacturer", match_operator="equals",
                         match_value="cisco", set_field="platform", set_value="Cisco FTD")
        result = result_for(manufacturer="")
        apply_rules(result, [by_name, by_vendor])
        assert result.primary.manufacturer == "Cisco"
        assert result.primary.platform == "Cisco FTD"

    def test_every_stack_member_is_judged_on_its_own(self):
        result = ScanResult(host="10.0.0.1", virtual_chassis_name="stack", devices=[
            DeviceRecord(name="stack", model="", vc_position=1, vc_is_master=True),
            DeviceRecord(name="stack-2", model="C9300-48P", vc_position=2),
            DeviceRecord(name="stack-3", model="", vc_position=3),
        ])
        applied = apply_rules(result, [rule(match_value="stack", set_value="C9300-24P")])
        assert [d.model for d in result.devices] == ["C9300-24P", "C9300-48P", "C9300-24P"]
        assert [a.device for a in applied] == ["stack", "stack-3"]

    def test_access_points_are_left_alone(self):
        result = result_for()
        result.access_points = [DeviceRecord(name="fw-ap-01", model="", is_access_point=True)]
        apply_rules(result, [rule()])
        assert result.access_points[0].model == ""

    def test_no_rules_is_a_no_op(self):
        result = result_for()
        assert apply_rules(result, []) == []
        assert result.rules_applied == []


class TestARealScan:
    def test_a_recorded_walk_can_be_filled_in(self):
        """End to end through the collector: a platform the device did not
        name, supplied by a rule matching its sysDescr."""
        result = build_scan_result(collect_fixture("palo-pa3220"))
        assert result.primary.model == "PA-3220"  # the device reports this itself
        reported = result.primary.software_version
        assert reported
        apply_rules(result, [
            rule(match_field="sys_descr", match_value="palo alto",
                 set_field="software_version", set_value="never"),
            rule(match_field="sys_descr", match_value="palo alto",
                 set_field="model", set_value="never"),
        ])
        # Both came from the device, so neither rule may touch them.
        assert result.primary.software_version == reported
        assert result.primary.model == "PA-3220"
        assert result.rules_applied == []


class TestThePayload:
    def test_the_scan_report_carries_what_rules_supplied(self):
        result = result_for()
        apply_rules(result, [rule()])
        payload = scan_payload(result)
        assert payload["devices"][0]["model"] == "FPR-2120"
        assert payload["rules_applied"] == [{
            "rule": "Firepower by name", "device": "fw-dal-01",
            "field": "model", "value": "FPR-2120", "previous": "",
        }]

    def test_no_rules_means_an_empty_list_not_a_missing_key(self):
        assert scan_payload(result_for())["rules_applied"] == []


class TestReadingRulesFromThePlugin:
    """The API renders choices as {value, label}; the poller must unwrap them."""

    API_ITEM = {
        "id": 3, "name": "Firepower by name", "enabled": True, "weight": 50,
        "match_field": {"value": "name", "label": "Device name"},
        "match_operator": {"value": "contains", "label": "contains"},
        "match_value": "fw-",
        "set_field": {"value": "model", "label": "Model"},
        "set_value": "FPR-2120", "only_if_blank": True,
    }

    def test_a_rule_as_the_api_renders_it(self):
        assert rule_from_api(self.API_ITEM) == rule(weight=50)

    def test_bare_values_are_accepted_too(self):
        flat = dict(self.API_ITEM, match_field="name", match_operator="contains",
                    set_field="model")
        assert rule_from_api(flat) == rule(weight=50)

    @pytest.mark.parametrize("broken", [
        {"match_field": "colour"},
        {"match_operator": "sounds_like"},
        {"set_field": "name"},            # has an override of its own
        {"set_field": "interfaces"},
        {"match_value": ""},
        {"set_value": "  "},
        {"match_operator": "regex", "match_value": "("},
    ])
    def test_a_rule_this_build_cannot_apply_is_skipped_not_fatal(self, broken, caplog):
        with caplog.at_level(logging.WARNING):
            assert rule_from_api(dict(self.API_ITEM, **broken)) is None
        assert "ignoring discovery rule" in caplog.text

    def test_rules_come_back_sorted_by_weight_then_name(self):
        class FakeNetBox:
            def all(self, path, params=None):
                assert path == rules_module.RULES_ENDPOINT
                assert params == {"enabled": "true"}
                return [
                    dict(self.API_ITEM, id=1, name="zeta", weight=100),
                    dict(self.API_ITEM, id=2, name="alpha", weight=100),
                    dict(self.API_ITEM, id=3, name="last-word", weight=10),
                ]
            API_ITEM = self.API_ITEM
        assert [r.name for r in load_rules(FakeNetBox())] == ["last-word", "alpha", "zeta"]

    def test_a_netbox_without_the_plugin_means_no_rules(self, caplog):
        class NoPlugin:
            def all(self, path, params=None):
                raise NetBoxError("GET %s -> 404: not found" % path)
        with caplog.at_level(logging.DEBUG):
            assert load_rules(NoPlugin()) == []
        assert "could not read" not in caplog.text

    def test_any_other_failure_is_said_out_loud(self, caplog):
        """Rules that exist and are silently not applied would be worse than
        none at all — a token missing view permission must be visible."""
        class Forbidden:
            def all(self, path, params=None):
                raise NetBoxError("GET %s -> 403: permission denied" % path)
        with caplog.at_level(logging.WARNING):
            assert load_rules(Forbidden()) == []
        assert "could not read discovery rules" in caplog.text


class TestApplyingAfterReview:
    """The apply re-reads the device and refuses if the hardware changed since
    review. That comparison must see what the device said, not what a rule
    filled in, or every rule-filled model looks like a swapped box."""

    class Reviewed:
        def __init__(self, devices, rules_applied=()):
            self.discovered = {"devices": devices, "rules_applied": list(rules_applied)}

        def get(self, path):
            return {"discovered": self.discovered}

    APPLIED = [{"rule": "Firepower by name", "device": "fw-dal-01",
                "field": "model", "value": "FPR-2120", "previous": ""}]

    def test_a_model_a_rule_supplied_is_not_a_hardware_change(self):
        reviewed = self.Reviewed(
            [{"name": "fw-dal-01", "serial": "JAD12345678", "model": "FPR-2120"}],
            self.APPLIED,
        )
        # The re-read, before rules run again: the device still reports none.
        assert _hardware_changed(reviewed, 1, result_for()) == ""

    def test_a_preview_from_before_rules_existed_still_compares_cleanly(self):
        """An older poller reported no model and a reviewer typed one; the
        override is not part of the preview and must not look like a change."""
        reviewed = self.Reviewed(
            [{"name": "fw-dal-01", "serial": "JAD12345678", "model": ""}],
        )
        assert _hardware_changed(reviewed, 1, result_for()) == ""

    def test_a_box_that_now_reports_a_different_model_is_still_caught(self):
        reviewed = self.Reviewed(
            [{"name": "fw-dal-01", "serial": "JAD12345678", "model": "FPR-2120"}],
            self.APPLIED,
        )
        message = _hardware_changed(reviewed, 1, result_for(model="FPR-2130"))
        assert "changed since this was reviewed" in message
        assert "FPR-2130" in message

    def test_a_different_serial_is_still_caught(self):
        reviewed = self.Reviewed(
            [{"name": "fw-dal-01", "serial": "OTHER", "model": "FPR-2120"}],
            self.APPLIED,
        )
        assert "changed since this was reviewed" in _hardware_changed(reviewed, 1, result_for())

    def test_a_serial_a_rule_supplied_is_not_a_hardware_change_either(self):
        reviewed = self.Reviewed(
            [{"name": "fw-dal-01", "serial": "FAZ-VMTM12345678", "model": "FPR-2120"}],
            [{"rule": "fw-dal-01 serial", "device": "fw-dal-01", "field": "serial",
              "value": "FAZ-VMTM12345678", "previous": ""}],
        )
        # The re-read, before rules: the device still reports no serial.
        assert _hardware_changed(reviewed, 1, result_for(model="FPR-2120", serial="")) == ""
        # But a box that now reports a serial of its own, and a different
        # one, is a different box.
        assert _hardware_changed(reviewed, 1, result_for(model="FPR-2120", serial="REAL1"))

    def test_a_model_the_device_reported_is_compared_as_before(self):
        reviewed = self.Reviewed(
            [{"name": "fw-dal-01", "serial": "JAD12345678", "model": "FPR-2130"}],
        )
        assert _hardware_changed(reviewed, 1, result_for(model="FPR-2130")) == ""
        assert _hardware_changed(reviewed, 1, result_for(model="FPR-2140"))
