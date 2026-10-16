"""Space recovery removes uploaded installers only, after the ready gate."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops.upgrade import f5, f5_workflow
from test_upgrade_f5 import IMAGE_BYTES, ISO, iso, options, profile, run_unit, unit


OLD = "BIGIP-16.1.4-0.0.9.iso"
OTHER = "BIGIP-17.1.1.3-0.0.5.iso"


def low_space(unit, monkeypatch):
    unit.images[OLD] = {"bytes": b"old installer", "verified": "yes"}
    unit.images[OTHER] = {"bytes": b"another installer", "verified": "yes"}
    monkeypatch.setattr(f5, "free_space_kb", lambda _: 0 if OLD in unit.images else 2_000_000)


@pytest.mark.parametrize("stage_only", [False, True])
def test_low_space_dry_run_plans_cleanup_without_deleting(profile, options, unit, monkeypatch, stage_only):
    low_space(unit, monkeypatch)
    options.stage_only = stage_only
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed and stages[-1] == "dry_run_complete"
    assert result.result["image_cleanup"]["candidates"] == [OLD, OTHER]
    assert not any(method == "DELETE" for method, _, _ in unit.calls)
    assert ISO not in unit.images


@pytest.mark.parametrize("stage_only", [False, True])
def test_low_space_removes_only_needed_installers_then_uploads(profile, options, unit, monkeypatch, stage_only):
    low_space(unit, monkeypatch)
    options.apply, options.stage_only = True, stage_only
    volumes = deepcopy(unit.volumes)
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed and result.changed, reporter.emit.call_args.args[2]
    assert OLD not in unit.images and OTHER in unit.images
    assert bytes(unit.images[ISO]["bytes"]) == IMAGE_BYTES
    assert [(method, path) for method, path, _ in unit.calls if method == "DELETE"] == [
        ("DELETE", f"/mgmt/tm/sys/software/image/{OLD}")]
    assert stages.index("ready") < stages.index("staging")
    cleanup = reporter.emit.call_args.args[3]["upgrade_plan"]["image_cleanup"]
    assert cleanup["removed"] == [OLD] and cleanup["failed"] == []
    assert cleanup["free_bytes_after"] >= cleanup["required_free_bytes"]
    if stage_only:
        assert unit.volumes == volumes
        assert not unit.ucs and unit.rebooted_to is None


def test_ready_gate_failure_never_deletes(profile, options, unit, monkeypatch):
    low_space(unit, monkeypatch)
    options.apply = options.stage_only = True
    host = SimpleNamespace(name="unit", hostname="192.0.2.10", platform="f5_tmsh", username="admin", password="secret")
    reporter = Mock()
    reporter.emit.side_effect = lambda host, stage, *args, **kwargs: stage != "ready"
    result = f5_workflow.upgrade_device(SimpleNamespace(host=host), profile, options, reporter)
    assert result.failed
    assert OLD in unit.images and ISO not in unit.images
    assert not any(method == "DELETE" for method, _, _ in unit.calls)


def test_enough_space_preserves_old_installers(profile, options, unit):
    unit.images[OLD] = {"bytes": b"old installer", "verified": "yes"}
    options.apply = options.stage_only = True
    result, _, _ = run_unit(profile, options)
    assert not result.failed and OLD in unit.images and ISO in unit.images
    assert not any(method == "DELETE" for method, _, _ in unit.calls)


def test_existing_target_is_preserved_when_freeing_reserve(profile, options, unit, monkeypatch):
    low_space(unit, monkeypatch)
    unit.images[ISO] = {"bytes": IMAGE_BYTES, "verified": "yes"}
    options.apply = options.stage_only = True
    upload = Mock(side_effect=AssertionError("target must not be uploaded again"))
    monkeypatch.setattr(f5, "upload_image", upload)
    result, _, _ = run_unit(profile, options)
    assert not result.failed and result.changed
    assert unit.images[ISO]["bytes"] == IMAGE_BYTES and OLD not in unit.images
    upload.assert_not_called()


@pytest.mark.parametrize("failure", ["insufficient", "unreadable", "delete_failed"])
def test_cleanup_failure_stops_before_upload(profile, options, unit, monkeypatch, failure):
    low_space(unit, monkeypatch)
    if failure == "delete_failed":
        original = unit.request
        def request(method, *args, **kwargs):
            if method == "DELETE":
                raise RuntimeError("delete refused")
            return original(method, *args, **kwargs)
        monkeypatch.setattr(unit, "request", request)
    else:
        monkeypatch.setattr(f5, "free_space_kb", lambda _: None if failure == "unreadable" and OLD not in unit.images else 0)
    options.apply = options.stage_only = True
    result, reporter, _ = run_unit(profile, options)
    assert result.failed and ISO not in unit.images
    assert unit.rebooted_to is None and not unit.ucs
    cleanup = reporter.emit.call_args.args[3]["upgrade_plan"]["image_cleanup"]
    if failure == "delete_failed":
        assert cleanup["failed"] == [OLD, OTHER]
    else:
        assert OLD in cleanup["removed"]


def test_staging_does_not_prune_during_installation(profile, options, unit, monkeypatch):
    low_space(unit, monkeypatch)
    unit.volumes[1]["status"] = "installing 20.000 pct"
    options.apply = options.stage_only = True
    result, reporter, _ = run_unit(profile, options)
    assert result.failed and "no installation in progress" in reporter.emit.call_args.args[2]
    assert not any(method == "DELETE" for method, _, _ in unit.calls)


def test_hotfix_cleanup_uses_hotfix_endpoint_and_preserves_target(monkeypatch):
    client = Mock(timeout=30)
    monkeypatch.setattr(f5, "listed_images", lambda _: [
        {"name": ISO, "kind": "image"}, {"name": "Hotfix-BIGIP-17.1.1-0.1.1.iso", "kind": "hotfix"}])
    monkeypatch.setattr(f5, "free_space_kb", lambda _: 0)
    removed, failed = f5.prune_images(client, ISO, 1024)
    assert removed == ["Hotfix-BIGIP-17.1.1-0.1.1.iso"] and failed == []
    client.request.assert_called_once_with("DELETE", "/mgmt/tm/sys/software/hotfix/Hotfix-BIGIP-17.1.1-0.1.1.iso", timeout=120)
