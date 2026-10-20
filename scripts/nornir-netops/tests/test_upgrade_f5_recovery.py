"""Recovery archives and WAF readiness gates, including long policy loading."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops.upgrade import checks, f5, f5_workflow, report, workflow
from test_upgrade_f5 import FakeBigIP, options, profile, iso, unit, run_unit


def policy(name="/Common/app", active=True):
    return {"fullPath": name, "active": active, "enforcementMode": "blocking"}


def waf_unit(monkeypatch, ready="1", policies=None):
    unit = FakeBigIP()
    original = unit.get_json
    def get(path):
        if path == "/mgmt/tm/sys/provision":
            return {"items": [{"name": "asm", "level": "nominal"}]}
        if path == f5.ASM_POLICIES:
            return {"items": [policy()] if policies is None else policies}
        return original(path)
    unit.get_json = get
    monkeypatch.setattr(f5, "bash", lambda *a, **k: ready)
    return unit


def test_non_waf_does_not_query_asm():
    unit = FakeBigIP()
    snapshot = f5.collect(unit, lambda _: None)
    assert "waf" not in snapshot
    assert not any("/asm/" in call[1] for call in unit.calls)


@pytest.mark.parametrize("ready", ["0", "", "error: module unavailable"])
def test_readable_policies_do_not_imply_ready(monkeypatch, ready, profile):
    snapshot = f5.collect(waf_unit(monkeypatch, ready), lambda _: None)
    assert snapshot["waf"]["policy_count"] == 1
    assert "waf_ready" in snapshot["errors"]
    assert f5_workflow.preflight(snapshot, profile)["blockers"]


def test_waf_comparison_uses_only_total_count(monkeypatch):
    before = f5.collect(waf_unit(monkeypatch), lambda _: None)
    after = deepcopy(before)
    after["tables"]["waf_policies"] = [{"fullPath": "/Common/parent", "type": "parent"}]
    assert not checks.compare(before, after)
    rendered = "\n".join(report.delta_section("Tables", before["tables"], after["tables"]))
    assert "waf_policies (count only) | 1 | 1" in rendered
    assert "### waf_policies" not in rendered


@pytest.mark.parametrize("count", [0, 2])
def test_waf_count_change_is_a_failure(monkeypatch, count):
    before = f5.collect(waf_unit(monkeypatch), lambda _: None)
    after = deepcopy(before)
    after["tables"]["waf_policies"] = [{} for _ in range(count)]
    finding = next(f for f in checks.compare(before, after) if f["check"] == "waf_policies")
    assert finding["severity"] == "error"
    assert (finding["before_count"], finding["after_count"]) == (1, count)


def test_all_policy_types_count_without_enforcement_fields(monkeypatch):
    policies = [policy(), {"fullPath": "/Common/parent", "type": "parent",
                           "active": None, "enforcementMode": None},
                {"type": "future-type"}, {}]
    snapshot = f5.collect(waf_unit(monkeypatch, policies=policies), lambda _: None)
    assert snapshot["waf"] == {"provisioned": True, "ready": True, "policy_count": 4}
    assert snapshot["metrics"]["counts"]["waf_policies"] == 4
    assert "waf_policies" not in snapshot["errors"]


def test_empty_policies_only_valid_when_explicit_and_ready(monkeypatch):
    snapshot = f5.collect(waf_unit(monkeypatch, policies=[]), lambda _: None)
    assert snapshot["waf"] == {"provisioned": True, "ready": True, "policy_count": 0}
    assert "waf_policies" not in snapshot["errors"]


def test_all_policy_pages_and_counts_are_verified():
    pages = {f5.ASM_POLICIES: {"items": [policy()], "totalItems": 2,
                             "nextLink": "https://localhost" + f5.ASM_POLICIES + "?$skip=1"},
             f5.ASM_POLICIES + "?$skip=1": {"items": [{"type": "parent"}], "totalItems": 2}}
    assert len(f5.waf_policies(pages.__getitem__)) == 2
    pages[f5.ASM_POLICIES + "?$skip=1"]["items"] = []
    with pytest.raises(ValueError, match="incomplete"):
        f5.waf_policies(pages.__getitem__)


@pytest.mark.parametrize("document", [{}, {"items": [None]}, {"items": "unreadable"},
    {"items": [], "nextLink": "https://localhost/mgmt/tm/sys"},
    {"items": [], "nextLink": f5.ASM_POLICIES}])
def test_bad_policy_responses_fail_closed(document):
    with pytest.raises(ValueError):
        f5.waf_policies(lambda _: document)


@pytest.mark.parametrize("header,body", [(None, b"x"), ("0-0/0", b""), ("0-0/3", b""),
                                         ("0-0/3", b"too long"), ("1-1/3", b"x")])
def test_bad_ucs_probe_leaves_no_archive(tmp_path, monkeypatch, header, body):
    monkeypatch.setattr(f5, "_raw", lambda *a, **k: SimpleNamespace(status_code=200,
                         headers={"Content-Range": header}, content=body))
    with pytest.raises(RuntimeError):
        f5.download_ucs(None, "backup.ucs", tmp_path / "backup.ucs")
    assert list(tmp_path.iterdir()) == []


def test_partial_download_cleans_temporary_file(tmp_path, monkeypatch):
    replies = iter([SimpleNamespace(status_code=200, headers={"Content-Range": "0-0/3"}, content=b"u"),
                    SimpleNamespace(status_code=200, headers={"Content-Range": "0-2/3"}, content=b"uc")])
    monkeypatch.setattr(f5, "_raw", lambda *a, **k: next(replies))
    with pytest.raises(RuntimeError):
        f5.download_ucs(None, "backup.ucs", tmp_path / "backup.ucs")
    assert list(tmp_path.iterdir()) == []


def test_backups_are_unique_private_verified_and_reported(profile, options, unit):
    host = SimpleNamespace(name="lab01")
    plans = [{}, {}]
    events = Mock()
    for plan in plans:
        f5_workflow.backup(unit, host, profile, options, plan, events)
    paths = [Path(p["ucs_backup"]["local_path"]) for p in plans]
    assert paths[0] != paths[1]
    assert all(p.read_bytes() == b"ucs" and p.stat().st_mode & 0o777 == 0o600 for p in paths)
    assert all(p["ucs_backup"]["md5_verified"] for p in plans)
    assert "ucs_backup" in events.call_args.args[2]["progress_summary"]


def test_bad_backup_prevents_install(profile, options, unit, monkeypatch):
    options.apply = True
    original = f5.remote_md5
    monkeypatch.setattr(f5, "remote_md5", lambda client, name, directory=f5.IMAGES:
                        "0" * 32 if directory == f5.UCS_DIR else original(client, name, directory))
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and "installing" not in stages and unit.rebooted_to is None
    assert not list((options.lock_dir.parent / "ucs-backups").glob("*.ucs"))


def test_phase_progress_is_ordered(profile, options, unit):
    options.apply = True
    result, reporter, _ = run_unit(profile, options)
    assert not result.failed
    phases = [call.args[3]["progress_summary"]["phase"] for call in reporter.emit.call_args_list]
    steps = [p["step"] for p in phases]
    assert steps == sorted(steps) and steps[-1] == 9
    assert all(p["total"] == 9 for p in phases)
    assert all(p["elapsed_seconds"] >= p["phase_elapsed_seconds"] >= 0 for p in phases)
    assert any(p["step"] == 7 for p in phases)


def test_waf_can_take_over_an_hour_to_load(monkeypatch, options):
    before = f5.collect(waf_unit(monkeypatch), lambda _: None)
    clock = [0]
    monkeypatch.setattr(workflow.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(workflow.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(workflow, "comparison_report", lambda *a: ({}, None))
    options.validation_timeout, options.poll_interval = 7200, 30
    events = Mock()
    def after():
        snapshot = deepcopy(before)
        if clock[0] < 3630:
            snapshot["waf"]["ready"] = False
            snapshot["errors"]["waf_ready"] = "ASM loading"
        return snapshot
    result = workflow.converge(events, options, before, {}, Mock(), SimpleNamespace(name="lab"), after, lambda _: [])
    assert not result.failed and clock[0] == 3660
    comparisons = [c for c in events.call_args_list if c.args[0] == "validating"]
    assert comparisons[-1].args[2]["progress_summary"]["consecutive_clean"] == 2
    assert comparisons[0].args[2]["progress_summary"]["waf"] == {
        "expected_policies": 1, "observed_policies": 1, "ready": False}


def test_verified_ucs_can_be_shared_with_a_private_web_group(monkeypatch, profile, options, unit):
    import grp
    import os
    monkeypatch.setenv('NETOPS_UCS_READ_GROUP', 'private-test-group')
    monkeypatch.setattr(grp, 'getgrnam', lambda name: SimpleNamespace(gr_gid=os.getgid()))
    plan = {}
    f5_workflow.backup(unit, SimpleNamespace(name='device'), profile, options, plan, Mock())
    path = Path(plan['ucs_backup']['local_path'])
    assert path.stat().st_mode & 0o777 == 0o640
    assert plan['ucs_backup']['md5_verified']


def test_baseline_progress_includes_waf_count_and_readiness(monkeypatch, profile, options, unit):
    collect = f5.collect
    def observed(client, progress):
        snapshot = collect(client, progress)
        snapshot['waf'] = {'provisioned': True, 'policy_count': 20, 'ready': True}
        snapshot['metrics']['counts']['waf_policies'] = 20
        return snapshot
    monkeypatch.setattr(f5, 'collect', observed)
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed
    baseline = next(c.args[3]['progress_summary'] for c in reporter.emit.call_args_list if c.args[1] == 'precheck_complete')
    assert baseline['waf'] == {'provisioned': True, 'policy_count': 20, 'ready': True}
    assert baseline['counts']['waf_policies'] == 20
