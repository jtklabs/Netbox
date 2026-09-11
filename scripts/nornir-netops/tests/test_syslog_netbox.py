"""Real CLI, NetBox client, Nornir and templates; simulated network endpoints."""

import copy
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from nornir.core.task import Result

from netops import cli, netbox, runner
from netops.features.syslog import parse_logging
from test_waf import NetBoxSession, OLD_DATE


class InventorySession(NetBoxSession):
    def __init__(self):
        super().__init__()
        self.devices = {}
        for id, name, platform in ((7, "ios", "cisco-ios-xe"), (8, "eos", "arista-eos")):
            device = copy.deepcopy(self.device)
            device.update(id=id, name=name, platform={"slug": platform},
                          primary_ip4={"address": f"192.0.2.{id}/24"})
            self.devices[id] = device
        self.interfaces = {"syslog-source": [
            {"name": "Loopback0", "device": {"id": 7}},
            {"name": "Management1", "device": {"id": 8}},
        ]}

    def request(self, method, url, json=None, params=None, **kwargs):
        path = urlsplit(url).path.removeprefix("/api/")
        if path not in ("dcim/devices/", "dcim/interfaces/") and not path.startswith("dcim/devices/"):
            return super().request(method, url, json=json, params=params, **kwargs)
        self.calls.append((method, path, copy.deepcopy(json or params)))
        status = 200
        if path == "dcim/devices/":
            body = {"results": list(self.devices.values()), "next": None}
        elif path == "dcim/interfaces/":
            body = {"results": self.interfaces.get(params["tag"], []), "next": None}
        else:
            device = self.devices[int(path.rstrip("/").rsplit("/", 1)[1])]
            if method == "PATCH":
                if self.fail_stamp:
                    status = 403
                elif not self.ignore_stamp:
                    device["custom_fields"].update(json["custom_fields"])
            body = device
        return SimpleNamespace(status_code=status, text="denied" if status == 403 else "",
                               json=lambda: copy.deepcopy(body))


class Switch:
    def __init__(self):
        self.lines = ["logging host 192.0.2.99", "logging trap warnings",
                      "logging source-interface Loopback9", "logging buffered 32768 debugging"]
        self.reads, self.writes, self.saves = [], [], 0
        self.ignore_source = self.ignore_remove = self.reject = self.fail_save = self.fail_read = False

    def apply(self, commands):
        self.writes.extend(commands)
        if self.reject:
            return "% Invalid input detected"
        for command in commands:
            if command.startswith("no "):
                if not self.ignore_remove and command[3:] in self.lines:
                    self.lines.remove(command[3:])
                continue
            parsed = parse_logging(command)
            if parsed and parsed[0].data["kind"] != "host":
                entry = parsed[0]
                if self.ignore_source and entry.data["kind"] == "source":
                    continue
                self.lines = [line for line in self.lines if not any(
                    e.data["kind"] == entry.data["kind"] and e.data.get("vrf") == entry.data.get("vrf")
                    for e in parse_logging(line))]
            if command not in self.lines:
                self.lines.append(command)
        return "\n".join(commands)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    session = InventorySession()
    boxes = {"ios": Switch(), "eos": Switch()}
    real_client = netbox.Client

    def client(*args, **kwargs):
        obj = real_client(*args, **kwargs)
        obj._session = session
        return obj

    def show(task, command_string, **kwargs):
        box = boxes[task.host.name]
        if command_string.startswith("show "):
            box.reads.append(command_string)
            output = "% Authorization failed" if box.fail_read else "\n".join(box.lines)
        else:
            box.saves += 1
            if box.fail_save:
                raise RuntimeError("save failed")
            output = "[OK]"
        return Result(host=task.host, result=output)

    def configure(task, config_commands, **kwargs):
        return Result(host=task.host, result=boxes[task.host.name].apply(config_commands))

    monkeypatch.setattr(netbox, "Client", client)
    monkeypatch.setattr(runner, "netmiko_send_command", show)
    monkeypatch.setattr(runner, "netmiko_send_config", configure)
    monkeypatch.setenv("NET_USER", "test-login")
    monkeypatch.setenv("NET_PASS", "test-password")
    monkeypatch.setenv("NETBOX_URL", "https://netbox.example")
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    standards = tmp_path / "standards.json"
    standards.write_text(json.dumps({"syslog": {"destinations": ["192.0.2.50"],
        "severity": "informational", "origin_id": "hostname", "source": "Loopback99"}}))
    report = tmp_path / "report.json"

    def run(*flags):
        if report.exists():
            report.unlink()
        code = cli.main(["syslog", "--no-env-file", "--no-log-file", "--netbox", "--yes",
                         "--no-rollback-file", "--standards", str(standards),
                         "--report", str(report), *flags])
        return code, json.loads(report.read_text()) if report.exists() else None

    return SimpleNamespace(nb=session, boxes=boxes, run=run, standards=standards, tmp_path=tmp_path)


def test_dry_run_loads_tagged_sources_without_any_writes(setup):
    code, report = setup.run("--policy", "netbox")
    assert code == cli.EXIT_OK
    assert report["mode"] == "netbox-tags"
    for name, source in (("ios", "Loopback0"), ("eos", "Management1")):
        row = report["devices"][name]
        assert f"logging source-interface {source}" in row["commands"]
        assert "source:Loopback99" not in row["desired"]
        assert row["syslog_compliant"] is False
        assert row["netbox_writeback"]["status"] == "planned"
        assert setup.boxes[name].writes == [] and setup.boxes[name].saves == 0
    assert setup.nb.writes == []
    assert [args["tag"] for method, path, args in setup.nb.calls if path == "dcim/interfaces/"] == ["syslog-source"]


@pytest.mark.parametrize("policy", ["audit", "add", "manage"])
def test_each_platform_applies_policy_and_reports_exact_compliance(setup, policy):
    before = {name: sorted(box.lines) for name, box in setup.boxes.items()}
    code, report = setup.run("--policy", policy, "--apply")
    assert code == cli.EXIT_OK
    for name, source in (("ios", "Loopback0"), ("eos", "Management1")):
        row = report["devices"][name]
        box = setup.boxes[name]
        assert row["mode"] == ("replace" if policy == "manage" else policy)
        assert row["syslog_compliant"] is (policy == "manage")
        assert row["netbox_writeback"]["status"] == "written"
        if policy == "audit":
            assert box.writes == [] and box.saves == 0
        else:
            assert f"logging source-interface {source}" in box.lines
            assert "logging trap informational" in box.lines
            assert ("logging host 192.0.2.99" in box.lines) is (policy == "add")
            assert row["verified"] and box.saves == 1
        assert "logging buffered 32768 debugging" in box.lines
        if name == "eos":
            assert not any("origin-id" in line for line in box.writes)
    code, again = setup.run("--policy", policy, "--apply")
    assert code == cli.EXIT_OK
    if policy != "audit":
        assert all(not row["commands"] for row in again["devices"].values())
        assert all(box.saves == 1 for box in setup.boxes.values())

    # The first apply's archive must restore both collector and source settings.
    for name, row in report["devices"].items():
        assert row["backout"]["complete"]
        setup.boxes[name].apply([step["command"] for step in row["backout"]["steps"]
                                if step["purpose"] == "restore"])
        assert sorted(setup.boxes[name].lines) == before[name]


def test_mixed_device_tags_resolve_independently(setup):
    setup.nb.devices[7]["tags"] = [{"slug": "syslog-add"}]
    setup.nb.devices[8]["tags"] = [{"slug": "syslog-manage"}]
    code, report = setup.run("--policy", "netbox", "--apply")
    assert code == cli.EXIT_OK
    assert report["devices"]["ios"]["mode"] == "add"
    assert report["devices"]["eos"]["mode"] == "replace"
    assert "logging host 192.0.2.99" in setup.boxes["ios"].lines
    assert "logging host 192.0.2.99" not in setup.boxes["eos"].lines
    assert setup.nb.devices[7]["custom_fields"]["syslog_compliant"] is False
    assert setup.nb.devices[8]["custom_fields"]["syslog_compliant"] is True


@pytest.mark.parametrize("tags", [[], [{"slug": "syslog-audit"}]])
def test_untagged_and_audit_tag_are_read_only_with_apply(setup, tags):
    for device in setup.nb.devices.values():
        device["tags"] = tags
    code, report = setup.run("--apply")
    assert code == cli.EXIT_OK
    assert all(not box.writes and not box.saves for box in setup.boxes.values())
    assert all(d["custom_fields"]["syslog_last_checked"] != OLD_DATE for d in setup.nb.devices.values())

    for row in report["devices"].values():
        assert row["action"] == "audit" and row["action_from_netbox"]
        assert row["netbox_policy_tag"] == ("syslog-audit" if tags else None)
        assert not row["implementation"]["will_execute"]
        assert row["result_after"]["status"] == "not_changed"


def test_conflicting_tags_fail_only_that_device_before_ssh(setup):
    setup.nb.devices[7]["tags"] = [{"slug": "syslog-add"}, {"slug": "syslog-manage"}]
    code, report = setup.run("--apply")
    assert code == cli.EXIT_FAILED
    assert report["devices"]["ios"]["status"] == "failed"
    assert setup.boxes["ios"].reads == setup.boxes["ios"].writes == []
    assert setup.nb.devices[7]["custom_fields"]["syslog_last_checked"] == OLD_DATE
    assert setup.nb.devices[8]["custom_fields"]["syslog_compliant"] is True

    row = report["devices"]["ios"]
    assert row["action"] is None and row["action_from_netbox"]
    assert row["current_config"]["status"] == "unavailable"
    assert not row["backout"]["complete"]


def test_duplicate_source_tags_fail_only_ambiguous_device(setup):
    setup.nb.interfaces["syslog-source"].append({"name": "Vlan10", "device": {"id": 7}})
    code, report = setup.run("--apply")
    assert code == cli.EXIT_FAILED
    assert "2 interfaces are tagged syslog-source" in report["devices"]["ios"]["error"]
    assert setup.boxes["ios"].reads == []
    assert setup.nb.devices[7]["custom_fields"]["syslog_last_checked"] == OLD_DATE
    assert setup.nb.devices[8]["custom_fields"]["syslog_compliant"] is True


def test_no_source_tag_preserves_current_source_and_ignores_fleet_default(setup):
    setup.nb.interfaces = {}
    code, report = setup.run("--apply")
    assert code == cli.EXIT_OK
    for name, row in report["devices"].items():
        assert row["syslog_compliant"] is True
        assert not any("source-interface" in command for command in row["commands"])
        assert "logging source-interface Loopback9" in setup.boxes[name].lines


@pytest.mark.parametrize("failure", ["ignore_source", "ignore_remove", "reject", "fail_save", "fail_read"])
def test_failed_device_check_preserves_its_netbox_fields(setup, failure):
    setattr(setup.boxes["ios"], failure, True)
    code, report = setup.run("--apply")
    assert code == cli.EXIT_FAILED
    assert report["devices"]["ios"].get("checked_at") is None
    assert setup.nb.devices[7]["custom_fields"]["syslog_last_checked"] == OLD_DATE
    if failure != "fail_save":
        assert setup.boxes["ios"].saves == 0
    assert setup.nb.devices[8]["custom_fields"]["syslog_compliant"] is True

    if failure == "reject":
        row = report["devices"]["ios"]
        assert row["current_config"]["status"] == "observed"
        assert row["implementation"]["steps"] and row["backout"]["steps"]
        assert row["result_after"]["status"] == "unknown"
        assert row["result_after"]["error"]
        assert report["devices"]["eos"]["result_after"]["status"] == "observed"


def test_no_verify_no_save_does_not_stamp(setup):
    code, report = setup.run("--apply", "--no-verify", "--no-save")
    assert code == cli.EXIT_OK
    assert all(row["checked_at"] is None for row in report["devices"].values())
    assert all(not box.saves for box in setup.boxes.values())
    assert setup.nb.writes == []


def test_invalid_custom_fields_fail_before_ssh(setup):
    setup.nb.fields[0]["type"] = "text"
    assert setup.run("--apply")[0] == cli.EXIT_USAGE
    assert all(not box.reads for box in setup.boxes.values())


def test_custom_source_tag_and_policy_from_env(setup, monkeypatch):
    setup.nb.interfaces["logging-source"] = setup.nb.interfaces.pop("syslog-source")
    monkeypatch.setenv("NETBOX_SYSLOG_SOURCE_TAG", "logging-source")
    monkeypatch.setenv("NETOPS_SYSLOG_POLICY", "manage")
    monkeypatch.setenv("NETOPS_F5_POLICY", "audit")
    assert setup.run("--apply")[0] == cli.EXIT_OK
    assert all(d["custom_fields"]["syslog_compliant"] is True for d in setup.nb.devices.values())


def test_netbox_writeback_failure_preserves_verified_device_result(setup):
    setup.nb.fail_stamp = True
    code, report = setup.run("--apply")
    assert code == cli.EXIT_FAILED
    assert all(row["verified"] for row in report["devices"].values())
    assert all(row["netbox_writeback"]["status"] == "failed" for row in report["devices"].values())


def test_vrf_sources_render_and_verify_on_both_platforms(setup):
    doc = json.loads(setup.standards.read_text())
    doc["syslog"]["vrf"] = "MGMT"
    setup.standards.write_text(json.dumps(doc))
    code, report = setup.run("--apply")
    assert code == cli.EXIT_OK
    assert "logging source-interface Loopback0 vrf MGMT" in setup.boxes["ios"].lines
    assert "logging vrf MGMT source-interface Management1" in setup.boxes["eos"].lines
    assert all(row["verified"] and row["syslog_compliant"] for row in report["devices"].values())
    for box in setup.boxes.values():
        box.writes.clear()
    assert setup.run("--apply")[0] == cli.EXIT_OK
    assert all(not box.writes for box in setup.boxes.values())


def test_source_mismatch_alone_is_not_compliant(setup):
    for box in setup.boxes.values():
        box.lines = ["logging host 192.0.2.50", "logging trap informational",
                     "logging source-interface Loopback9", "logging origin-id hostname"]
    code, report = setup.run("--policy", "audit", "--apply")
    assert code == cli.EXIT_OK
    for row in report["devices"].values():
        assert row["syslog_compliant"] is False
        assert len(row["syslog_audit"]["missing"]) == 1
        assert row["syslog_audit"]["missing"][0].startswith("source:")


def test_cli_source_tag_overrides_env(setup, monkeypatch):
    monkeypatch.setenv("NETBOX_SYSLOG_SOURCE_TAG", "wrong-source")
    code, report = setup.run("--syslog-source-tag", "syslog-source", "--apply")
    assert code == cli.EXIT_OK
    assert report["devices"]["ios"]["syslog_audit"]["source_interface"] == "Loopback0"


def test_explicit_policy_overrides_conflicting_switch_tags(setup):
    for device in setup.nb.devices.values():
        device["tags"] = [{"slug": "syslog-audit"}, {"slug": "syslog-add"}]
    code, report = setup.run("--policy", "manage", "--apply")
    assert code == cli.EXIT_OK
    assert all(row["syslog_compliant"] for row in report["devices"].values())


def test_rollback_restores_replaced_source_and_severity(setup):
    before = {name: list(box.lines) for name, box in setup.boxes.items()}
    code, report = setup.run("--apply")
    assert code == cli.EXIT_OK
    for name, box in setup.boxes.items():
        reversal = report["devices"][name]["rollback"]
        assert "logging source-interface Loopback9" in reversal
        assert "logging trap warnings" in reversal
        box.apply(reversal)
        assert sorted(box.lines) == sorted(before[name])


def test_no_save_still_verifies_and_stamps_observed_configuration(setup):
    code, report = setup.run("--apply", "--no-save")
    assert code == cli.EXIT_OK
    assert all(row["syslog_compliant"] and row["verified"] for row in report["devices"].values())
    assert all(not box.saves for box in setup.boxes.values())


def test_no_extra_destinations_reports_compliant_after_add_policy(setup):
    for box in setup.boxes.values():
        box.lines = ["logging trap warnings"]
    code, report = setup.run("--apply", "--policy", "add")
    assert code == cli.EXIT_OK
    assert all(row["syslog_compliant"] for row in report["devices"].values())


def test_mixed_cisco_arista_f5_inventory_uses_policy_per_device(setup, monkeypatch):
    from netops import f5_waf, f5_syslog
    from test_waf import FakeF5
    setup.nb.devices[7]["tags"] = [{"slug": "syslog-add"}]
    device = copy.deepcopy(setup.nb.devices[7])
    device.update(id=9, name="f5", platform={"slug": "f5-tmos"},
                  primary_ip4={"address": "192.0.2.9/24"}, tags=[{"slug": "syslog-audit"}])
    setup.nb.devices[9] = device
    f5 = FakeF5()
    monkeypatch.setattr(f5_waf, "Client", lambda host, **kwargs: f5)
    code, report = setup.run("--apply")
    assert code == cli.EXIT_OK
    assert {name: row["mode"] for name, row in report["devices"].items()} == {
        "ios": "add", "eos": "replace", "f5": "audit"}
    assert f5_syslog.SYSLOG in f5.reads
    assert f5.writes == []
    assert setup.boxes["ios"].writes and setup.boxes["eos"].writes
    assert all(row["netbox_writeback"]["status"] == "written" for row in report["devices"].values())


def test_netbox_source_name_is_validated_before_ssh(setup):
    setup.nb.interfaces["syslog-source"][0]["name"] = "Loopback0\nlogging host 203.0.113.1"
    code, report = setup.run("--apply")
    assert code == cli.EXIT_FAILED
    assert setup.boxes["ios"].reads == []
    assert report["devices"]["ios"]["status"] == "failed"


def test_aws_secret_key_mapping_supplies_switch_login(setup, monkeypatch):
    from netops import credentials
    calls, logins = [], []
    def fetch(name, region=None):
        calls.append((name, region))
        return {"ssh_user": "aws-user", "ssh_password": "aws-password"}
    original_show = runner.netmiko_send_command
    def show(task, **kwargs):
        logins.append((task.host.username, task.host.password))
        return original_show(task, **kwargs)
    monkeypatch.setattr(credentials, "fetch_json_secret", fetch)
    monkeypatch.setattr(runner, "netmiko_send_command", show)
    monkeypatch.delenv("NET_USER")
    monkeypatch.delenv("NET_PASS")
    code, _ = setup.run("--aws-secret", "test/network", "--aws-region", "us-east-1",
                        "--aws-username-key", "ssh_user", "--aws-password-key", "ssh_password")
    assert code == cli.EXIT_OK
    assert calls == [("test/network", "us-east-1")]
    assert logins and all(login == ("aws-user", "aws-password") for login in logins)
