"""Drain policies and upgrade recovery against fake BIG-IP endpoints only."""

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import unquote

import pytest

from netops.upgrade import f5_drain, f5_workflow
from netops.upgrade.profile import Profile
from test_upgrade_f5 import FakeBigIP, iso, options, profile, run_unit  # noqa: F401


class DrainBigIP(FakeBigIP):
    def __init__(self):
        super().__init__(failover="active", peers=())
        self.virtuals = {"/Common/vs-web": True, "/Tenant/app/vs api": True, "/Common/off": False}
        self.groups = []
        self.count = 100
        self.read_count = lambda: self.count
        self.patch_failure = None

    def _get(self, path):
        if path == "/mgmt/tm/cm/device-group":
            return {"items": deepcopy(self.groups)}
        if path == "/mgmt/tm/ltm/virtual":
            return {"items": [{"fullPath": n, "enabled" if enabled else "disabled": True}
                              for n, enabled in self.virtuals.items()]}
        if path == "/mgmt/tm/ltm/virtual/stats":
            count = self.read_count()
            return {"entries": {str(i): {"nestedStats": {"entries": {
                "tmName": {"description": name}, "clientside.curConns": {"value": count if i == 0 else 0},
                "status.availabilityState": {"description": "available"},
                "status.enabledState": {"description": "enabled" if enabled else "disabled"},
            }}} for i, (name, enabled) in enumerate(self.virtuals.items())}}
        return super()._get(path)

    def request(self, method, path, payload=None, timeout=None):
        if method == "PATCH" and path.startswith("/mgmt/tm/ltm/virtual/"):
            self.calls.append((method, path, payload))
            name = unquote(path.rsplit("/", 1)[-1]).replace("~", "/")
            self.virtuals[name] = bool(payload.get("enabled"))
            if self.patch_failure:
                self.patch_failure(name, payload)
            return {}
        return super().request(method, path, payload, timeout)


@pytest.fixture
def unit(monkeypatch):
    unit = DrainBigIP()
    monkeypatch.setattr(f5_workflow.f5_waf, "Client", unit)
    return unit


@pytest.fixture
def timer(monkeypatch):
    clock = SimpleNamespace(now=0)
    def sleep(seconds):
        assert seconds > 0
        clock.now += seconds
    monkeypatch.setattr(f5_drain, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    return clock


@pytest.mark.parametrize("counts,reason,elapsed", [
    ([100, 100, 50, 49], "plateau", 10),
    ([100, 100, 0], "zero_connections", 5),
    ([0, 0], "zero_connections", 0),
    ([100, 100, 100], "plateau", 5),
    ([100, 100, 95, 94], "plateau", 10),  # exactly 5% is still progress
])
def test_dynamic_stopping(profile, unit, timer, counts, reason, elapsed):
    profile = replace(profile, drain_connections=True, drain_window_seconds=5, drain_timeout_seconds=30)
    values = iter(counts)
    unit.read_count = lambda: next(values)
    record = {}
    f5_drain.drain(unit, profile, record, Mock(return_value=True))
    assert record["reason"] == reason and record["elapsed_seconds"] == elapsed
    assert record["restore_pending"] == ["/Common/vs-web", "/Tenant/app/vs api"]
    assert not any(unit.virtuals.values())
    f5_drain.restore(unit, record, Mock())
    assert unit.virtuals == {"/Common/vs-web": True, "/Tenant/app/vs api": True, "/Common/off": False}
    assert record["restore_pending"] == []


@pytest.mark.parametrize("counts", [[100, 100, 90, 80], [100, 100, 110, 120]])
def test_timeout_for_continued_decline_or_growth(profile, unit, timer, counts):
    profile = replace(profile, drain_connections=True, drain_window_seconds=5, drain_timeout_seconds=10)
    unit.read_count = iter(counts).__next__
    record = {}
    with pytest.raises(TimeoutError, match="reboot cancelled"):
        f5_drain.drain(unit, profile, record, Mock(return_value=True))
    assert record["status"] == "timed_out"


def test_growth_from_zero_is_not_a_plateau(profile, unit, timer):
    profile = replace(profile, drain_window_seconds=5, drain_timeout_seconds=5)
    unit.read_count = iter([0, 1, 2]).__next__
    with pytest.raises(TimeoutError):
        f5_drain.drain(unit, profile, {}, Mock(return_value=True))


def test_peer_on_later_page_blocks_drain():
    pages = {
        '/mgmt/tm/cm/device': {'items': [{'selfDevice': 'true', 'failoverState': 'active'}],
                              'nextLink': 'https://localhost/mgmt/tm/cm/device?$skip=1'},
        '/mgmt/tm/cm/device?$skip=1': {'items': [{'selfDevice': 'false', 'failoverState': 'standby'}]},
    }
    with pytest.raises(ValueError, match="standalone"):
        f5_drain.check_standalone(SimpleNamespace(get_json=pages.__getitem__))


def test_dry_run_reports_policy_without_disabling(profile, options, unit):
    result, reporter, stages = run_unit(replace(profile, drain_connections=True), options)
    assert not result.failed and stages[-1] == "dry_run_complete"
    assert not any(method == "PATCH" for method, _, _ in unit.calls)
    assert result.result["connection_drain"]["baseline_connections"] == 100
    assert '"disabled": true' in str(result.result["commands"])
    assert reporter.emit.call_args.args[3]["progress_summary"]["connection_drain"]["status"] == "planned"


@pytest.mark.parametrize("apply,stage_only,current", [(True, True, False), (True, False, True)])
def test_stage_only_and_already_current_do_not_drain(profile, options, unit, apply, stage_only, current):
    options.apply, options.stage_only = apply, stage_only
    if current:
        unit.version = profile.target_version
    result, _, _ = run_unit(replace(profile, drain_connections=True), options)
    assert not result.failed
    assert not any(method == "PATCH" for method, _, _ in unit.calls)
    assert not any(path == "/mgmt/tm/cm/device-group" for _, path, _ in unit.calls)


def test_full_upgrade_preserves_states_and_order(profile, options, unit, timer):
    options.apply = True
    initial = dict(unit.virtuals)
    result, reporter, stages = run_unit(replace(profile, drain_connections=True,
                                                drain_window_seconds=5), options)
    assert not result.failed, reporter.emit.call_args.args[2]
    assert unit.virtuals == initial and unit.rebooted_to == "HD1.2"
    assert stages.index("installing") < stages.index("draining") < stages.index("restoring_virtuals")
    assert "failing_over" not in stages
    assert unit.saved == 1  # temporary VIP changes are never saved
    assert reporter.emit.call_args.args[3]["progress_summary"]["connection_drain"]["restoration_pending"] == 0


def test_standalone_builtin_trust_group_does_not_block(profile, options, unit):
    unit.groups = [{"name": "device_trust_group", "type": "sync-only", "autoSync": "enabled"}]
    result, _, stages = run_unit(replace(profile, drain_connections=True), options)
    assert not result.failed and stages[-1] == "dry_run_complete"


@pytest.mark.parametrize("kind", ["active_peer", "standby_peer", "failover_group", "unknown_state"])
@pytest.mark.parametrize("apply", [False, True])
def test_cluster_drain_is_blocked_even_with_allow_active(profile, options, unit, kind, apply):
    options.apply = apply
    if kind in {"active_peer", "standby_peer"}:
        unit.peers = ["bigip-b"]
        unit.failover = "standby" if kind == "standby_peer" else "active"
    elif kind == "failover_group":
        unit.groups = [{"name": "ha", "type": "sync-failover", "autoSync": "disabled"}]
    else:
        unit.failover = "unknown"
    result, reporter, stages = run_unit(replace(profile, drain_connections=True, allow_active=True), options)
    assert result.failed and stages[-1] == "blocked"
    assert "standalone" in reporter.emit.call_args.args[2]
    assert unit.installing is None and unit.rebooted_to is None and unit.failovers == 0
    assert not any(method == "PATCH" for method, _, _ in unit.calls)


def test_cluster_membership_rechecked_immediately_before_disable(profile, options, unit, monkeypatch):
    options.apply = True
    original = f5_workflow.wait_for_install
    def install(*args):
        original(*args)
        unit.peers = ["new-peer"]
    monkeypatch.setattr(f5_workflow, "wait_for_install", install)
    result, reporter, stages = run_unit(replace(profile, drain_connections=True), options)
    assert result.failed and "standalone" in reporter.emit.call_args.args[2]
    assert unit.rebooted_to is None and unit.failovers == 0
    assert not any(method == "PATCH" for method, _, _ in unit.calls)


@pytest.mark.parametrize("failure", ["patch", "stats", "timeout"])
def test_failure_restores_all_touched_vips_and_does_not_reboot(profile, options, unit, timer, failure, monkeypatch):
    options.apply = True
    initial = dict(unit.virtuals)
    if failure == "patch":
        def fail(name, payload):
            if name == "/Tenant/app/vs api" and payload.get("disabled"):
                raise RuntimeError("lost PATCH response")
        unit.patch_failure = fail
    elif failure == "stats":
        def count():
            if not any(unit.virtuals.values()):
                raise RuntimeError("stats unavailable")
            return 100
        unit.read_count = count
    elif failure == "timeout":
        unit.read_count = lambda: 100 + timer.now
    result, reporter, stages = run_unit(replace(profile, drain_connections=True, allow_active=True,
                                                drain_window_seconds=5, drain_timeout_seconds=10), options)
    assert result.failed and stages[-1] == "recovery_required"
    assert unit.virtuals == initial and unit.rebooted_to is None
    assert reporter.emit.call_args.args[3]["upgrade_plan"]["connection_drain"]["restore_pending"] == []


def test_restore_failures_do_not_skip_other_vips(profile, options, unit, timer):
    options.apply = True
    def fail(name, payload):
        if payload.get("enabled") and name == "/Common/vs-web":
            raise RuntimeError("restore failed")
    unit.patch_failure = fail
    result, reporter, stages = run_unit(replace(profile, drain_connections=True, drain_window_seconds=5), options)
    assert result.failed and "manual" in reporter.emit.call_args.args[2]
    assert reporter.emit.call_args.args[3]["upgrade_plan"]["connection_drain"]["restore_pending"] == ["/Common/vs-web"]
    assert unit.virtuals["/Tenant/app/vs api"] is True


@pytest.mark.parametrize("reachable", [True, False])
def test_failed_reconnect_attempts_restoration_and_reports_unresolved_vips(
        profile, options, unit, timer, monkeypatch, reachable):
    options.apply = True
    initial = dict(unit.virtuals)
    def failed_target(*args):
        unit.down_until = 0 if reachable else float('inf')
        raise TimeoutError("reboot deadline exceeded")
    monkeypatch.setattr(f5_workflow, "wait_for_target", failed_target)
    result, reporter, stages = run_unit(replace(profile, drain_connections=True, drain_window_seconds=5), options)
    assert result.failed and stages[-1] == "recovery_required"
    if reachable:
        assert unit.virtuals == initial
        assert reporter.emit.call_args.args[3]["upgrade_plan"]["connection_drain"]["restore_pending"] == []
    else:
        assert "verify/restore original enabled state manually" in reporter.emit.call_args.args[2]


def test_progress_gate_failure_never_disables(profile, unit, timer):
    initial = dict(unit.virtuals)
    with pytest.raises(ValueError, match="webhook unavailable"):
        f5_drain.drain(unit, profile, {}, Mock(return_value=False))
    assert unit.virtuals == initial


def test_empty_inventory_is_noop(profile, unit, timer):
    unit.virtuals = {}
    record = {}
    f5_drain.drain(unit, profile, record, Mock(return_value=True))
    assert record['reason'] == 'zero_connections'
    assert record['restore_pending'] == []
    assert not any(method == "PATCH" for method, _, _ in unit.calls)


def test_malformed_collections_fail_closed():
    with pytest.raises(ValueError, match="items are missing"):
        f5_drain.inventory(SimpleNamespace(get_json=lambda path: {}))


def test_inventory_pagination_and_repeated_page_detection():
    documents = {
        '/mgmt/tm/ltm/virtual': {'items': [{'fullPath': '/Common/a', 'enabled': True}],
                               'nextLink': 'https://localhost/mgmt/tm/ltm/virtual?$skip=1'},
        '/mgmt/tm/ltm/virtual?$skip=1': {'items': [{'fullPath': '/Partition/app/b', 'disabled': True}]},
    }
    client = SimpleNamespace(get_json=documents.__getitem__)
    assert f5_drain.inventory(client) == [{'name': '/Common/a', 'enabled': True},
                                         {'name': '/Partition/app/b', 'enabled': False}]
    documents['/mgmt/tm/ltm/virtual?$skip=1']['nextLink'] = '/mgmt/tm/ltm/virtual'
    with pytest.raises(ValueError, match="repeated"):
        f5_drain.inventory(client)


@pytest.mark.parametrize("value", [None, -1, True, "10", 1.5])
def test_bad_statistics_never_mean_zero(unit, value):
    unit.read_count = lambda: value
    with pytest.raises(ValueError, match="statistics"):
        f5_drain.connections(unit, f5_drain.inventory(unit))


def test_partial_stats_and_pagination(unit):
    virtuals = f5_drain.inventory(unit)
    document = unit.get_json("/mgmt/tm/ltm/virtual/stats")
    entries = list(document["entries"].items())
    pages = {"/mgmt/tm/ltm/virtual/stats": {"entries": dict(entries[:1]),
             "nextLink": "https://localhost/mgmt/tm/ltm/virtual/stats?$skip=1"},
             "/mgmt/tm/ltm/virtual/stats?$skip=1": {"entries": dict(entries[1:])}}
    client = SimpleNamespace(get_json=lambda path: pages[path])
    assert f5_drain.connections(client, virtuals) == 100
    pages["/mgmt/tm/ltm/virtual/stats?$skip=1"]["entries"] = {}
    with pytest.raises(ValueError, match="incomplete"):
        f5_drain.connections(client, virtuals)


@pytest.mark.parametrize("change", [
    {"drain_connections": "yes"}, {"drain_min_reduction_percent": 0}, {"drain_min_reduction_percent": 101},
    {"drain_window_seconds": True}, {"drain_window_seconds": -1}, {"drain_timeout_seconds": 0},
    {"drain_timeout_seconds": 30}, {"drain_window_seconds": 1.5},
    {"image": "EOS-4.32.1F.swi", "drain_connections": True},
])
def test_profile_rejects_invalid_drain_settings(profile, change):
    data = {**asdict(profile), **change}
    data["models"], data["starting_versions"] = list(data["models"]), list(data["starting_versions"])
    with pytest.raises(ValueError):
        Profile.from_mapping(data)
