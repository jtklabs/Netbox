"""Profile limits are independent, exact and never waive missing evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from netops.upgrade import checks, f5, f5_drain, report
from netops.upgrade.profile import Profile
from test_upgrade_f5 import iso, profile, options, unit, run_unit


def snapshot():
    return {"tables": {key: [
        {("fullPath" if key == "waf_policies" else "name"): f"/Common/obj-{i}", "enabled": "enabled"}
        for i in range(10)] for key in checks.F5_THRESHOLDS},
        "routing": {}, "errors": {}, "warnings": {}}


@pytest.mark.parametrize("key", sorted(set(checks.F5_THRESHOLDS.values())))
@pytest.mark.parametrize("value", [-1, 101, True, False, None, "90", 90.5, float("nan"), float("inf")])
def test_invalid_thresholds(profile, key, value):
    data = asdict(profile)
    data.update(models=list(profile.models), starting_versions=list(profile.starting_versions))
    data[key] = value
    with pytest.raises(ValueError, match=key):
        Profile.from_mapping(data)


def test_defaults_and_non_f5_rejection(profile):
    assert profile.f5_ltm_min_unchanged_percent == profile.f5_waf_min_unchanged_percent == 100
    data = asdict(profile)
    data.update(models=["C9300-24P"], starting_versions=["17.9.4"], image="cat9k.bin",
                f5_ltm_min_unchanged_percent=90)
    with pytest.raises(ValueError, match="BIG-IP"):
        Profile.from_mapping(data)


@pytest.mark.parametrize("key,setting", checks.F5_THRESHOLDS.items())
def test_independent_threshold_boundary_and_report(key, setting):
    before = snapshot()
    after = deepcopy(before)
    after["tables"][key][0]["enabled"] = "disabled"
    strict = checks.compare(before, after)
    assert len(strict) == 1 and strict[0]["severity"] == "error"
    accepted = checks.compare(before, after, **{setting: 90})
    assert len(accepted) == 1 and accepted[0]["severity"] == "warning"
    assert accepted[0]["unchanged"] == 9
    assert accepted[0]["unchanged_percent"] == 90
    assert accepted[0]["changed"][0]["fields"]["enabled"]["after"] == "disabled"
    assert checks.compare(before, after, **{setting: 91})[0]["severity"] == "error"
    text = report.build(SimpleNamespace(name="lab", hostname="192.0.2.1"), before, after, accepted,
                        {"approved_profile": {setting: 90}}, "completed_with_warnings")
    assert f"| {key} | 9 / 10 | 90.00 | 90 | Pass |" in text
    assert "disabled" in text and "differences accepted by profile" in text


def test_categories_never_offset_each_other():
    before, after = snapshot(), snapshot()
    after["tables"]["pools"] = []
    findings = checks.compare(before, after, f5_ltm_min_unchanged_percent=90)
    assert len(findings) == 1 and findings[0]["check"] == "pools"
    assert findings[0]["severity"] == "error"


def test_additions_removals_and_same_count_replacements():
    old = snapshot()["tables"]["nodes"]
    added = old + [{"name": "/Common/new", "enabled": "enabled"}]
    assert checks.object_comparison("nodes", old, added, 91)["threshold_passed"] is False
    assert checks.object_comparison("nodes", old, added, 90)["threshold_passed"] is True
    assert checks.object_comparison("nodes", old, old[1:], 90)["threshold_passed"] is True
    replaced = old[1:] + [{"name": "/Common/replacement", "enabled": "enabled"}]
    result = checks.object_comparison("nodes", old, replaced, 90)
    assert result["total_objects"] == 11 and not result["threshold_passed"]
    assert checks.object_comparison("nodes", [], [], 100)["unchanged_percent"] == 100
    assert checks.object_comparison("nodes", [], old, 1)["threshold_passed"] is False
    assert checks.object_comparison("nodes", old, [], 0)["threshold_passed"] is True


@pytest.mark.parametrize("invalid", [None, {}, [{"name": "/Common/x"}, {"name": "/Common/x"}], [{}]])
def test_invalid_inventory_cannot_be_waived(invalid):
    before, after = snapshot(), snapshot()
    after["tables"]["nodes"] = invalid
    findings = checks.compare(before, after, f5_ltm_min_unchanged_percent=0)
    assert any(f["check"] == "nodes" and f["severity"] == "error" for f in findings)


def test_readiness_and_collection_errors_cannot_be_waived():
    before, after = snapshot(), snapshot()
    after["errors"] = {"waf_ready": "ASM still loading", "nodes": "read failed"}
    findings = checks.compare(before, after, f5_ltm_min_unchanged_percent=0, f5_waf_min_unchanged_percent=0)
    assert {f["check"] for f in findings if f["severity"] == "error"} == {"waf_ready", "nodes"}
    assert checks.threshold_summary(before, after, {})["nodes"]["threshold_passed"] is False


def test_drained_comparison_and_progress_ignore_only_deliberate_disable():
    before, after = snapshot(), snapshot()
    pending = [row["name"] for row in after["tables"]["virtual_servers"]]
    for row in after["tables"]["virtual_servers"]:
        row["enabled"] = "disabled"
    after["tables"]["nodes"][0]["enabled"] = "disabled"
    findings = f5_drain.compare_while_drained(before, after, {"restore_pending": pending},
                                             f5_ltm_min_unchanged_percent=90)
    assert len(findings) == 1 and findings[0]["severity"] == "warning"
    summary = checks.threshold_summary(before, after, {"f5_ltm_min_unchanged_percent": 90}, pending)
    assert summary["virtual_servers"]["unchanged_percent"] == 100
    assert summary["nodes"]["unchanged_percent"] == 90
    assert after["tables"]["virtual_servers"][0]["enabled"] == "disabled"
    after["tables"]["virtual_servers"][0]["enabled"] = "enabled"
    findings = f5_drain.compare_while_drained(before, after, {"restore_pending": pending},
                                             f5_ltm_min_unchanged_percent=0)
    assert any(f["check"] == "traffic_hold" and f["severity"] == "error" for f in findings)


@pytest.mark.parametrize("document", [{}, {"entries": []}, {"entries": {"x": {}}}])
def test_missing_stats_are_not_empty_inventories(document):
    with pytest.raises(ValueError):
        f5.rows_from_stats(document, {"name": "tmName", "enabled": "status.enabledState"})


def test_worker_uses_profile_and_retains_accepted_differences(profile, options, unit, monkeypatch):
    original = f5.collect
    def collect(client, progress):
        snap = original(client, progress)
        snap["tables"]["nodes"] = deepcopy(snapshot()["tables"]["nodes"])
        if unit.rebooted_to:
            snap["tables"]["nodes"][0]["enabled"] = "disabled"
        return snap
    monkeypatch.setattr(f5, "collect", collect)
    options.apply = True
    result, reporter, stages = run_unit(replace(profile, f5_ltm_min_unchanged_percent=90), options)
    assert not result.failed and result.result["status"] == "completed_with_warnings"
    assert result.result["findings"][0]["threshold_passed"]
    summaries = [c.args[3]["progress_summary"] for c in reporter.emit.call_args_list
                 if len(c.args) > 3 and isinstance(c.args[3], dict)
                 and "object_comparisons" in c.args[3].get("progress_summary", {})]
    assert summaries and summaries[-1]["object_comparisons"]["nodes"]["unchanged"] == 9
