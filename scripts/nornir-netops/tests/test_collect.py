"""Command collection: catalog, per-platform runs against a fake session, filing, and upload."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops import collect
from netops.cli import build_parser


def test_packaged_catalog_covers_every_requested_platform():
    catalog = collect.load_catalog()
    for platform in ("juniper_junos", "cisco_ios", "arista_eos", "aruba_os", "bluecoat_sgos", "checkpoint_gaia",
                     "fortinet", "infoblox_nios", "opengear_linux", "paloalto_panos", "f5_tmsh"):
        assert catalog[platform]["commands"], platform
    juniper = [c["command"] for c in catalog["juniper_junos"]["commands"]]
    assert "show configuration | display set" in juniper and "show dot1x interface detail" in juniper
    cisco = [c["command"] for c in catalog["cisco_ios"]["commands"]]
    assert "show authentication sessions" in cisco and "show running-config" in cisco
    assert catalog["cisco_ios"]["enable"] and not catalog["juniper_junos"]["enable"]
    assert catalog["opengear_linux"]["driver"] == "linux" and catalog["infoblox_nios"]["driver"] == "generic"
    assert next(c for c in catalog["cisco_ios"]["commands"] if c["command"] == "show running-config")["timeout"] == 300


def test_filenames_match_the_existing_convention():
    assert collect.slug("show configuration | display json") == "show_configuration_display_json"
    assert collect.slug("show ip int brief") == "show_ip_int_brief"
    assert collect.slug("config -g config.ports") == "config_g_config_ports"


@pytest.mark.parametrize("text,message", [
    ("cisco_ios: [show version]", "expected a mapping"),
    ("cisco_ios:\n  commands: []", "nonempty"),
    ("cisco_ios:\n  commands:\n    - {command: show version, timeout: 0}", "positive"),
    ("cisco_ios:\n  commands:\n    - show ip route\n    - show ip-route", "same filename"),
])
def test_catalog_validation(tmp_path, text, message):
    path = tmp_path / "commands.yaml"
    path.write_text(text)
    with pytest.raises(collect.CatalogError, match=message):
        collect.load_catalog(path)


def test_project_catalog_overrides_the_packaged_one(tmp_path):
    (tmp_path / "commands.yaml").write_text("cisco_ios:\n  commands: [show clock]\n")
    catalog = collect.load_catalog(None, tmp_path)
    assert [c["command"] for c in catalog["cisco_ios"]["commands"]] == ["show clock"] and "juniper_junos" not in catalog


class FakeConnection:
    def __init__(self, replies):
        self.replies, self.sent, self.enabled = replies, [], False

    def enable(self):
        self.enabled = True

    def send_command(self, command, read_timeout=None):
        self.sent.append((command, read_timeout))
        reply = self.replies.get(command, f"output of {command}")
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_task(platform="cisco_ios", replies=None, name="sw1"):
    connection = FakeConnection(replies or {})
    host = SimpleNamespace(name=name, hostname="192.0.2.1", platform=platform, data={"netbox_id": 42},
                           get_connection=lambda kind, config: connection, close_connection=lambda kind: None)
    return SimpleNamespace(host=host, nornir=SimpleNamespace(config=None)), connection


def test_commands_are_run_filed_and_rejections_recorded(tmp_path, monkeypatch):
    from netops.debuglog import protect
    protect(["s3cret-pass"])
    catalog = {"cisco_ios": {"driver": "cisco_ios", "enable": True, "commands": [
        {"command": "show version", "timeout": 60}, {"command": "show running-config", "timeout": 300},
        {"command": "show ip bgp summary", "timeout": 60}, {"command": "show dot1x all", "timeout": 60}]}}
    task, connection = make_task(replies={"show running-config": "hostname sw1\nusername admin password s3cret-pass\nend",
                                          "show ip bgp summary": "% BGP not active",
                                          "show dot1x all": TimeoutError("read timed out")})
    result = collect.collect_commands(task, catalog, tmp_path)
    assert not result.failed and connection.enabled
    assert connection.sent[1] == ("show running-config", 300)
    outputs = {o["command"]: o for o in result.result["outputs"]}
    assert outputs["show version"]["ok"] and outputs["show version"]["filename"] == "sw1_show_version.txt"
    assert not outputs["show ip bgp summary"]["ok"] and outputs["show ip bgp summary"]["error"].startswith("% BGP not active")
    assert not outputs["show dot1x all"]["ok"] and "TimeoutError" in outputs["show dot1x all"]["error"]
    stored = (tmp_path / "sw1" / "sw1_show_running_config.txt").read_text()
    assert "s3cret-pass" not in stored and "hostname sw1" in stored
    assert outputs["show running-config"]["size"] == len(stored.encode()) and len(outputs["show running-config"]["sha256"]) == 64
    assert task.host.platform == "cisco_ios"


def test_driver_override_and_unknown_platform(tmp_path):
    catalog = {"opengear_linux": {"driver": "linux", "enable": False, "commands": [{"command": "cat /etc/version", "timeout": 30}]}}
    seen = []
    task, _ = make_task(platform="opengear_linux", name="con1")
    task.host.get_connection = lambda kind, config: seen.append(task.host.platform) or FakeConnection({})
    result = collect.collect_commands(task, catalog, tmp_path)
    assert seen == ["linux"] and task.host.platform == "opengear_linux"
    assert result.result["outputs"][0]["filename"] == "con1_cat_etc_version.txt"
    task, _ = make_task(platform="cisco_asa")
    result = collect.collect_commands(task, catalog, tmp_path)
    assert result.result["skipped"] and not result.result["outputs"]


def test_upload_posts_each_file_and_flags_oversized_ones(tmp_path):
    task, _ = make_task(replies={"show version": "v", "show running-config": "x" * 50})
    catalog = {"cisco_ios": {"driver": "cisco_ios", "enable": False,
                             "commands": [{"command": "show version", "timeout": 1}, {"command": "show running-config", "timeout": 1}]}}
    records = collect.collect_commands(task, catalog, tmp_path).result
    client = Mock()
    sent, oversized = collect.upload(client, task.host, records, "checkmk-us", max_bytes=20)
    assert (sent, oversized) == (2, 1)
    payloads = [call.args[2] for call in client.request_object.call_args_list]
    assert payloads[0]["device"] == 42 and payloads[0]["content"] == "v" and payloads[0]["poller"] == "checkmk-us"
    assert payloads[0]["filename"] == "sw1_show_version.txt" and payloads[0]["ok"]
    assert payloads[1]["content"] == "" and not payloads[1]["ok"] and "kept on the poller" in payloads[1]["error"]
    assert json.dumps(payloads)  # serializable


def test_cli_wiring():
    args = build_parser().parse_args(["collect", "--netbox", "--commands", "x.yaml", "--no-upload"])
    assert args.command == "collect" and args.commands == "x.yaml" and args.no_upload
    assert build_parser().parse_args(["collect", "--ip", "10.0.0.1", "--platform", "cisco_ios"]).output_dir is None
    assert not args.no_ntp_discovery
    assert build_parser().parse_args(["collect", "--no-ntp-discovery"]).no_ntp_discovery


@pytest.mark.parametrize("platform,source_line,server_line,interface,vrf_line,vrf", [
    ("cisco_ios", "ntp source Loopback0", "ntp server 192.0.2.1", "Loopback0", "", "default"),
    ("cisco_xe", "ntp source Loopback0", "ntp server 192.0.2.1", "Loopback0", "", "default"),
    ("cisco_nxos", "ntp source-interface mgmt0", "ntp server 192.0.2.1 use-vrf management",
     "mgmt0", "vrf member management", "management"),
    ("arista_eos", "ntp local-interface vrf MGMT Management1", "ntp server vrf MGMT 192.0.2.1",
     "Management1", "vrf MGMT", "MGMT"),
])
def test_collection_discovers_source_on_existing_ssh_session(tmp_path, platform, source_line, server_line,
                                                            interface, vrf_line, vrf):
    from netops.core import canonical_platform
    from netops.ntp_discovery import SHOW_COMMANDS
    task, connection = make_task(platform=platform, replies={
        SHOW_COMMANDS[0]: server_line, SHOW_COMMANDS[1]: source_line, SHOW_COMMANDS[2]: "",
        f"show running-config interface {interface}": f"interface {interface}\n {vrf_line}\n!",
    })
    task.host.get_connection = Mock(return_value=connection)
    catalog = {canonical_platform(platform): {"driver": canonical_platform(platform), "enable": True,
                                             "commands": [{"command": "show version", "timeout": 60}]}}
    result = collect.collect_commands(task, catalog, tmp_path)
    observation = result.result["ntp_discovery"]
    assert observation["status"] == "resolved"
    assert (observation["source"], observation["vrf"]) == (interface, vrf)
    assert observation["checked_at"]
    task.host.get_connection.assert_called_once()
    assert all(command.startswith("show ") for command, _ in connection.sent)
    assert not result.changed


@pytest.mark.parametrize("reply", [TimeoutError("SSH timeout"), "% Invalid input detected"])
def test_ntp_failure_does_not_discard_inventory_outputs(tmp_path, reply):
    from netops.ntp_discovery import SHOW_COMMANDS
    task, _ = make_task(replies={SHOW_COMMANDS[0]: reply})
    result = collect.collect_commands(task, collect.load_catalog(), tmp_path)
    assert result.result["outputs"] and not result.failed
    assert result.result["ntp_discovery"]["status"] == "error"


@pytest.mark.parametrize("platform,enabled", [("cisco_ios", False), ("f5_tmsh", True), ("aruba_os", True)])
def test_disabled_and_unsupported_source_discovery_is_skipped(tmp_path, platform, enabled):
    from netops.ntp_discovery import SHOW_COMMANDS
    task, connection = make_task(platform=platform)
    result = collect.collect_commands(task, collect.load_catalog(), tmp_path, discover_ntp=enabled)
    assert "ntp_discovery" not in result.result
    assert not any(command in SHOW_COMMANDS for command, _ in connection.sent)


@pytest.mark.parametrize("mode,expected", [("write", 0), ("preview", 0), ("conflict", 2),
                                          ("ssh-error", 1), ("field-error", 1), ("disabled", 0)])
def test_collector_run_syncs_and_reports_ntp_separately(monkeypatch, tmp_path, mode, expected):
    from nornir.core.task import MultiResult, Result
    from netops import cli, ntp_discovery
    from netops.netbox import NetBoxError
    from test_ntp_discovery import Client

    client = Client()
    host = SimpleNamespace(name="sw1", hostname="192.0.2.10", data={"netbox_id": 1})
    observation = ntp_discovery.source_config("ntp source Lo0\nntp server 192.0.2.1", "cisco_ios")
    if mode == "conflict":
        client.device["custom_fields"]["ntp_vrf"] = "MANUAL"
    if mode == "ssh-error":
        observation = {"status": "error", "reason": "SSH timeout"}
    if mode == "field-error":
        monkeypatch.setattr(ntp_discovery, "ensure_fields", Mock(side_effect=NetBoxError("permission denied")))
    result = MultiResult("collect")
    data = {"platform": "cisco_ios", "outputs": [], "ntp_discovery": observation}
    if mode == "disabled":
        del data["ntp_discovery"]
    result.append(Result(host=host, result=data))
    targets = SimpleNamespace(inventory=SimpleNamespace(hosts={"sw1": host}),
                              run=Mock(return_value={"sw1": result}))
    monkeypatch.setattr(cli, "_connect", lambda args, style: (targets, SimpleNamespace(describe=lambda: "test"), 0))
    monkeypatch.setattr(collect, "netbox_client", lambda args: client)
    uploaded = Mock(return_value=(0, 0))
    monkeypatch.setattr(collect, "upload", uploaded)
    records = {}
    monkeypatch.setattr(collect.archive, "capture", lambda data: records.update(data))
    args = build_parser().parse_args(["collect", "--netbox", "--no-standards", "--output-dir", str(tmp_path)]
                                     + (["--no-upload"] if mode == "preview" else [])
                                     + (["--no-ntp-discovery"] if mode == "disabled" else []))
    assert collect.run(args, cli.Style(False), Mock()) == expected
    assert targets.run.call_args.kwargs["discover_ntp"] == (mode != "disabled")
    if mode in ("preview", "disabled", "field-error"):
        assert client.writes == []
    if mode != "preview":
        uploaded.assert_called_once()
    else:
        uploaded.assert_not_called()
    if mode == "write":
        assert client.device["custom_fields"]["ntp_vrf"] == "default"
        assert any(tag["slug"] == "service-source" for tag in client.interfaces[0]["tags"])
        assert records["sw1"]["ntp_writeback"]["status"] == "written"
    elif mode == "conflict":
        assert client.device["custom_fields"]["ntp_vrf"] == "MANUAL"
        assert records["sw1"]["ntp_writeback"]["status"] == "attention"
        assert not any(tag["slug"] == "service-source" for tag in client.interfaces[0]["tags"])
    elif mode == "ssh-error":
        assert client.device["custom_fields"]["ntp_discovery"]["status"] == "error"
        assert "ntp_vrf" not in client.device["custom_fields"]
