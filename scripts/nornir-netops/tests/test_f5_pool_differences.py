"""Pool identity stays stable when availability, admin state or membership changes."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from netops.upgrade import checks, f5, report
from test_upgrade_f5 import FakeBigIP


def pool(name="/Common/web", **fields):
    return {"name": name, "availability": "available", "enabled": "enabled", "active_members": 4, **fields}


def compare(old, new):
    before = f5.collect(FakeBigIP(), lambda _: None)
    before["tables"]["pools"] = old
    after = deepcopy(before)
    after["tables"]["pools"] = new
    return before, after, checks.compare(before, after)


@pytest.mark.parametrize("field,value,description", [
    ("active_members", 3, "active members: 4 → 3"),
    ("availability", "offline", "availability: available → offline"),
    ("enabled", "disabled", "enabled: enabled → disabled"),
    ("active_members", None, "active members: 4 → unavailable"),
])
def test_state_change_is_reported_as_changed_and_still_fails_validation(field, value, description):
    before, after, findings = compare([pool()], [pool(**{field: value})])
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "error"
    assert finding["removed"] == finding["added"] == []
    assert finding["changed"] == [{"name": "/Common/web", "fields": {
        field: {"before": before["tables"]["pools"][0][field], "after": value}}}]
    progress = checks.describe_findings(findings)
    assert "changed /Common/web" in progress and description in progress
    assert "removed" not in progress and "added" not in progress
    markdown = report.build(SimpleNamespace(name="bigip", hostname="192.0.2.1"),
                            before, after, findings, {}, "validation_failed")
    assert "| pools | 1 | 1 | 0 | 0 | 0 | 1 |" in markdown
    assert "Changed (1):\n- /Common/web (" + description + ")" in markdown
    assert "Removed (" not in markdown and "Added (" not in markdown
    html = report.to_html(markdown, "Pool changes")
    assert "<th>Changed</th>" in html and description in html


def test_mixed_additions_removals_and_multiple_changed_fields():
    old = [pool("/Common/unchanged"), pool("/Common/removed"), pool()]
    new = [pool(active_members=2, enabled="disabled", availability="offline"),
           pool("/Common/new"), pool("/Common/unchanged")]
    _, _, findings = compare(old, new)
    finding = findings[0]
    assert finding["before_count"] == finding["after_count"] == 3
    assert finding["unchanged"] == 1
    assert finding["removed"] == [pool("/Common/removed")]
    assert finding["added"] == [pool("/Common/new")]
    assert set(finding["changed"][0]["fields"]) == {"active_members", "availability", "enabled"}
    text = "\n".join(report.delta_section("Tables", {"pools": old}, {"pools": new}))
    assert "| pools | 3 | 3 | 1 | 1 | 1 | 1 |" in text
    assert "active members: 4 → 2" in text
    assert "availability: available → offline" in text
    assert "enabled: enabled → disabled" in text
    assert "Removed (1):\n- name=/Common/removed" in text
    assert "Added (1):\n- name=/Common/new" in text


def test_full_partition_path_is_identity_and_order_is_irrelevant():
    old = [pool("/Common/web"), pool("/Tenant/web")]
    assert compare(old, list(reversed(old)))[2] == []
    _, _, findings = compare(old, [pool("/Tenant/web", active_members=2)])
    assert findings[0]["removed"] == [pool("/Common/web")]
    assert findings[0]["changed"][0]["name"] == "/Tenant/web"


@pytest.mark.parametrize("old,new", [([pool()], []), ([], [pool()]), ([], [])])
def test_empty_collections_are_real_inventory_changes(old, new):
    delta = checks.pool_delta(old, new)
    assert delta["removed"] == old and delta["added"] == new
    assert delta["changed"] == []


@pytest.mark.parametrize("invalid", [[pool(name=None)], [pool(), pool()]])
@pytest.mark.parametrize("side", ["before", "after"])
def test_ambiguous_identity_fails_without_claiming_a_pool_was_removed(invalid, side):
    old, new = (invalid, [pool()]) if side == "before" else ([pool()], invalid)
    _, _, findings = compare(old, new)
    assert findings[0]["severity"] == "error"
    assert "cannot reliably match" in findings[0]["message"]
    text = "\n".join(report.delta_section("Tables", {"pools": old}, {"pools": new}))
    assert "cannot reliably match" in text
    assert "Removed (" not in text and "Added (" not in text


def test_missing_collection_is_unavailable_not_removed():
    text = "\n".join(report.delta_section("Tables", {"pools": [pool()]}, {}))
    assert "not collected on one side" in text and "Removed (" not in text


def test_html_escapes_pool_names_and_field_values():
    text = "\n".join(report.delta_section("Tables",
        {"pools": [pool("/Common/<web>")]},
        {"pools": [pool("/Common/<web>", availability="<offline>")]}))
    html = report.to_html(text, "Pools")
    assert "/Common/&lt;web&gt;" in html and "&lt;offline&gt;" in html
    assert "<web>" not in html and "<offline>" not in html
